import oci
import csv
import sys
import os
import shutil
from datetime import datetime

# ==========================================
# 1. Initialize Configuration & Output Dir
# ==========================================
try:
    config = oci.config.from_file(
        file_location=os.environ.get("OCI_CLI_CONFIG_FILE", "/etc/oci/config"),
        profile_name=os.environ.get("OCI_CLI_PROFILE")
    )
    tenancy_id = config["tenancy"]
    identity_client = oci.identity.IdentityClient(config)
except Exception as e:
    print(f"Error loading OCI config: {e}")
    sys.exit(1)

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
OUT_DIR = f"oci_export_{timestamp}"
os.makedirs(OUT_DIR, exist_ok=True)

# ==========================================
# 2. Core Helper Functions
# ==========================================
def write_csv(filename, headers, data):
    if not data:
        return
    filepath = os.path.join(OUT_DIR, filename)
    with open(filepath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(data)
    print(f"  -> Wrote {len(data)} rows to {filename}")

def get_compartment_map():
    print("Mapping compartments...")
    name_map = {tenancy_id: "root"}
    comps = oci.pagination.list_call_get_all_results(
        identity_client.list_compartments, tenancy_id, 
        compartment_id_in_subtree=True, access_level="ANY"
    ).data
    for c in comps:
        name_map[c.id] = c.name
    return name_map

def generic_export(client_class, list_method_name, comp_map, csv_name, extract_fields):
    """
    A unified engine to iterate through compartments and extract OCI resources.
    extract_fields is a dict of {"CSV Column Name": "oci_object_attribute_name"}
    """
    try:
        client = client_class(config)
        method = getattr(client, list_method_name)
    except AttributeError:
        print(f"  [!] SDK does not support {list_method_name}. Skipping.")
        return

    data_list = []
    for comp_id, comp_name in comp_map.items():
        try:
            results = oci.pagination.list_call_get_all_results(method, comp_id).data
            for item in results:
                # Filter out terminated resources to keep reports clean
                if hasattr(item, 'lifecycle_state') and item.lifecycle_state in ["TERMINATED", "DELETED", "TERMINATING"]:
                    continue
                
                row = {"Compartment": comp_name}
                for csv_col, obj_attr in extract_fields.items():
                    val = getattr(item, obj_attr, "N/A")
                    row[csv_col] = str(val) if val is not None else ""
                data_list.append(row)
        except oci.exceptions.ServiceError:
            pass # Skip compartments where we lack permissions
    
    headers = ["Compartment"] + list(extract_fields.keys())
    write_csv(csv_name, headers, data_list)

# ==========================================
# 3. Specific Export Modules
# ==========================================
def export_identityOptions(comp_map):
    print("\n--- Exporting Identity ---")
    try:
        domains = oci.pagination.list_call_get_all_results(identity_client.list_domains, compartment_id=tenancy_id).data
        user_list, group_list = [], []
        
        for domain in domains:
            try:
                domain_client = oci.identity_domains.IdentityDomainsClient(config, service_endpoint=domain.url)
                
                # Users SCIM Loop
                start_index, count = 1, 50
                while True:
                    response = domain_client.list_users(start_index=start_index, count=count, attribute_sets=["all"])
                    resources = response.data.resources if response.data.resources else []
                    for u in resources:
                        email = u.emails[0].value if getattr(u, 'emails', None) else "None"
                        user_groups = [f"[{domain.display_name}] {g.display}" for g in u.groups] if getattr(u, 'groups', None) else []
                        user_list.append({
                            "Name": getattr(u, 'user_name', "Unknown"), "Email": email, "Domain": domain.display_name,
                            "Groups": " ; ".join(user_groups), "OCID": getattr(u, 'ocid', getattr(u, 'id', 'Unknown'))
                        })
                    if len(resources) < count: break
                    start_index += count
                    
                # Groups SCIM Loop
                start_index = 1
                while True:
                    response = domain_client.list_groups(start_index=start_index, count=count, attribute_sets=["all"])
                    resources = response.data.resources if response.data.resources else []
                    for g in resources:
                        group_list.append({
                            "Name": getattr(g, 'display_name', getattr(g, 'non_unique_display_name', "Unknown")),
                            "Description": getattr(g, 'description', "N/A"), "Domain": domain.display_name,
                            "OCID": getattr(g, 'ocid', getattr(g, 'id', 'Unknown'))
                        })
                    if len(resources) < count: break
                    start_index += count
            except: pass

        write_csv("identity_users.csv", ["Name", "Email", "Domain", "Groups", "OCID"], user_list)
        write_csv("identity_groups.csv", ["Name", "Description", "Domain", "OCID"], group_list)
    except:
        print("  [!] Error mapping identity domains via SCIM.")

    # Policies
    policy_list = []
    for comp_id, comp_name in comp_map.items():
        try:
            policies = oci.pagination.list_call_get_all_results(identity_client.list_policies, comp_id).data
            for p in policies:
                policy_list.append({"Name": p.name, "Compartment": comp_name, "Statements": " ; ".join(p.statements)})
        except: continue
    write_csv("identity_policies.csv", ["Name", "Compartment", "Statements"], policy_list)

def export_governance(comp_map):
    print("\n--- Exporting Governance ---")
    generic_export(oci.limits.QuotasClient, "list_quotas", comp_map, "gov_quotas.csv", {"Name": "name", "Statements": "statements", "OCID": "id"})

def export_cost_management(comp_map):
    print("\n--- Exporting Cost Management ---")
    generic_export(oci.budget.BudgetClient, "list_budgets", comp_map, "cost_budgets.csv", {"Name": "display_name", "Target Type": "target_type", "Amount": "amount"})

def export_network(comp_map):
    print("\n--- Exporting Network ---")
    fields = {"Name": "display_name", "State": "lifecycle_state", "CIDR": "cidr_block", "OCID": "id"}
    generic_export(oci.core.VirtualNetworkClient, "list_vcns", comp_map, "network_vcns.csv", fields)
    generic_export(oci.core.VirtualNetworkClient, "list_subnets", comp_map, "network_subnets.csv", fields)

def export_firewall_policies(comp_map):
    print("\n--- Exporting OCI Firewall ---")
    generic_export(oci.network_firewall.NetworkFirewallClient, "list_network_firewalls", comp_map, "firewalls.csv", {"Name": "display_name", "Policy OCID": "network_firewall_policy_id"})

def export_dns_management(comp_map):
    print("\n--- Exporting DNS Management ---")
    generic_export(oci.dns.DnsClient, "list_zones", comp_map, "dns_zones.csv", {"Name": "name", "Zone Type": "zone_type"})

def export_compute(comp_map):
    print("\n--- Exporting Compute ---")
    generic_export(oci.core.ComputeClient, "list_instances", comp_map, "compute_instances.csv", {"Name": "display_name", "State": "lifecycle_state", "Shape": "shape", "OCID": "id"})
    generic_export(oci.core.ComputeClient, "list_dedicated_vm_hosts", comp_map, "compute_dedicated_hosts.csv", {"Name": "display_name", "State": "lifecycle_state", "Shape": "dedicated_vm_host_shape"})

def export_storage(comp_map):
    print("\n--- Exporting Storage ---")
    generic_export(oci.core.BlockstorageClient, "list_volumes", comp_map, "storage_block_volumes.csv", {"Name": "display_name", "Size(GB)": "size_in_gbs", "State": "lifecycle_state"})
    generic_export(oci.file_storage.FileStorageClient, "list_file_systems", comp_map, "storage_fss.csv", {"Name": "display_name", "State": "lifecycle_state"})
    
    # Object Storage requires namespace lookup
    try:
        os_client = oci.object_storage.ObjectStorageClient(config)
        namespace = os_client.get_namespace().data
        bucket_list = []
        for comp_id, comp_name in comp_map.items():
            try:
                buckets = oci.pagination.list_call_get_all_results(os_client.list_buckets, namespace, comp_id).data
                for b in buckets:
                    bucket_list.append({"Compartment": comp_name, "Name": b.name, "Namespace": b.namespace})
            except: pass
        write_csv("storage_buckets.csv", ["Compartment", "Name", "Namespace"], bucket_list)
    except Exception as e:
        print(f"  [!] Failed to extract Object Storage: {e}")

def export_databases(comp_map):
    print("\n--- Exporting Databases ---")
    generic_export(oci.database.DatabaseClient, "list_db_systems", comp_map, "db_systems.csv", {"Name": "display_name", "Shape": "shape", "State": "lifecycle_state"})
    generic_export(oci.database.DatabaseClient, "list_autonomous_databases", comp_map, "db_autonomous.csv", {"Name": "display_name", "Workload": "db_workload", "Storage(TB)": "data_storage_size_in_tbs"})

def export_loadbalancer(comp_map):
    print("\n--- Exporting Load Balancers ---")
    generic_export(oci.load_balancer.LoadBalancerClient, "list_load_balancers", comp_map, "load_balancers_lbr.csv", {"Name": "display_name", "Shape": "shape_name"})
    generic_export(oci.network_load_balancer.NetworkLoadBalancerClient, "list_network_load_balancers", comp_map, "load_balancers_nlb.csv", {"Name": "display_name"})

def export_management_services(comp_map):
    print("\n--- Exporting Management Services ---")
    generic_export(oci.ons.NotificationControlPlaneClient, "list_topics", comp_map, "mgmt_sns_topics.csv", {"Name": "name", "Description": "description"})
    generic_export(oci.monitoring.MonitoringClient, "list_alarms", comp_map, "mgmt_alarms.csv", {"Name": "display_name", "Severity": "severity"})

def export_developer_services(comp_map):
    print("\n--- Exporting Developer Services ---")
    generic_export(oci.container_engine.ContainerEngineClient, "list_clusters", comp_map, "dev_oke_clusters.csv", {"Name": "name", "K8s Version": "kubernetes_version"})

def export_security(comp_map):
    print("\n--- Exporting Security ---")
    generic_export(oci.key_management.KmsVaultClient, "list_vaults", comp_map, "sec_kms_vaults.csv", {"Name": "display_name", "Vault Type": "vault_type", "State": "lifecycle_state"})

def export_sddc(comp_map):
    print("\n--- Exporting SDDC ---")
    generic_export(oci.ocvp.SddcClient, "list_sddcs", comp_map, "sddc_clusters.csv", {"Name": "display_name", "HCX Status": "hcx_state", "State": "lifecycle_state"})

def export_all(comp_map):
    for key in range(2, 16):
        MENU_OPTIONS[key][1](comp_map)

# ==========================================
# 4. Menu System & Execution
# ==========================================
MENU_OPTIONS = {
    1: ("Export All OCI Resources", export_all),
    2: ("Export Identity", export_identityOptions),
    3: ("Export Governance", export_governance),
    4: ("Export Cost Management", export_cost_management),
    5: ("Export Network", export_network),
    6: ("Export OCI Firewall", export_firewall_policies),
    7: ("Export DNS Management", export_dns_management),
    8: ("Export Compute", export_compute),
    9: ("Export Storage", export_storage),
    10: ("Export Databases", export_databases),
    11: ("Export Load Balancers", export_loadbalancer),
    12: ("Export Management Services", export_management_services),
    13: ("Export Developer Services", export_developer_services),
    14: ("Export Security", export_security),
    15: ("Export Software-Defined Data Centers - OCVS", export_sddc)
}

def display_menu():
    print("\n" + "="*50)
    print("      Dependency-Free OCI Resource Exporter (Cloudshell)")
    print("="*50)
    for key, (name, _) in MENU_OPTIONS.items():
        print(f"{key:>2}. {name}")
    print(" q. Quit")
    print("="*50)

def main():
    while True:
        display_menu()
        choice = input("Enter your choice: ").strip().lower()
        
        if choice == 'q':
            # Cleanup empty dirs if cancelled early
            if not os.listdir(OUT_DIR):
                os.rmdir(OUT_DIR)
            print("Exiting...")
            sys.exit(0)
            
        try:
            choice_int = int(choice)
            if choice_int in MENU_OPTIONS:
                comp_map = get_compartment_map()
                
                # Execute the mapped function
                MENU_OPTIONS[choice_int][1](comp_map)
                
                # Zip the output directory
                zip_path = shutil.make_archive(OUT_DIR, 'zip', OUT_DIR)
                print(f"\n[SUCCESS] Export complete. Files zipped to: {zip_path}")
                break
            else:
                print("Invalid option. Try again.")
        except ValueError:
            print("Invalid input. Please enter a number or 'q'.")

if __name__ == "__main__":
    main()
