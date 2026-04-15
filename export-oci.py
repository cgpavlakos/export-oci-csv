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
    config = oci.config.from_file()
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
        identity_client.list_compartments, 
        compartment_id=tenancy_id, 
        compartment_id_in_subtree=True, 
        access_level="ANY"
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
            results = oci.pagination.list_call_get_all_results(method, compartment_id=comp_id).data
            for item in results:
                if hasattr(item, 'lifecycle_state') and item.lifecycle_state in ["TERMINATED", "DELETED", "TERMINATING"]:
                    continue
                
                row = {"Compartment": comp_name}
                for csv_col, obj_attr in extract_fields.items():
                    val = getattr(item, obj_attr, "N/A")
                    row[csv_col] = str(val) if val is not None else ""
                data_list.append(row)
        except oci.exceptions.ServiceError:
            pass 
    
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
                
                # Users
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
                    
                # Groups
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
            policies = oci.pagination.list_call_get_all_results(
                identity_client.list_policies, compartment_id=comp_id
            ).data
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
    print("\n--- Exporting Network (Advanced) ---")
    vn_client = oci.core.VirtualNetworkClient
    base_fields = {"Name": "display_name", "State": "lifecycle_state", "OCID": "id"}
    
    # Core VCN
    generic_export(vn_client, "list_vcns", comp_map, "network_vcns.csv", {"Name": "display_name", "State": "lifecycle_state", "CIDR": "cidr_block", "OCID": "id"})
    generic_export(vn_client, "list_subnets", comp_map, "network_subnets.csv", {"Name": "display_name", "State": "lifecycle_state", "CIDR": "cidr_block", "OCID": "id"})
    
    # Gateways
    generic_export(vn_client, "list_drgs", comp_map, "network_drgs.csv", base_fields)
    generic_export(vn_client, "list_internet_gateways", comp_map, "network_igws.csv", base_fields)
    generic_export(vn_client, "list_nat_gateways", comp_map, "network_nat_gateways.csv", base_fields)
    generic_export(vn_client, "list_service_gateways", comp_map, "network_service_gateways.csv", base_fields)
    generic_export(vn_client, "list_local_peering_gateways", comp_map, "network_lpgs.csv", base_fields)
    
    # Security & Routing
    generic_export(vn_client, "list_route_tables", comp_map, "network_route_tables.csv", base_fields)
    generic_export(vn_client, "list_security_lists", comp_map, "network_security_lists.csv", base_fields)
    generic_export(vn_client, "list_network_security_groups", comp_map, "network_nsgs.csv", base_fields)
    
    # Connectivity
    generic_export(vn_client, "list_ip_sec_connections", comp_map, "network_ipsec_vpns.csv", base_fields)
    generic_export(vn_client, "list_virtual_circuits", comp_map, "network_fastconnect.csv", base_fields)

def export_firewall_policies(comp_map):
    print("\n--- Exporting OCI Firewall ---")
    generic_export(oci.network_firewall.NetworkFirewallClient, "list_network_firewalls", comp_map, "firewalls.csv", {"Name": "display_name", "Policy OCID": "network_firewall_policy_id"})

def export_dns_management(comp_map):
    print("\n--- Exporting DNS Management ---")
    generic_export(oci.dns.DnsClient, "list_zones", comp_map, "dns_zones.csv", {"Name": "name", "Zone Type": "zone_type"})

def export_compute(comp_map):
    print("\n--- Exporting Compute (Advanced) ---")
    comp_client = oci.core.ComputeClient
    generic_export(comp_client, "list_instances", comp_map, "compute_instances.csv", {"Name": "display_name", "State": "lifecycle_state", "Shape": "shape", "OCID": "id"})
    generic_export(comp_client, "list_dedicated_vm_hosts", comp_map, "compute_dedicated_hosts.csv", {"Name": "display_name", "State": "lifecycle_state", "Shape": "dedicated_vm_host_shape"})
    generic_export(comp_client, "list_images", comp_map, "compute_custom_images.csv", {"Name": "display_name", "State": "lifecycle_state", "OS": "operating_system", "Size(MB)": "size_in_mbs"})
    generic_export(oci.core.ComputeManagementClient, "list_instance_pools", comp_map, "compute_instance_pools.csv", {"Name": "display_name", "State": "lifecycle_state", "Size": "size"})

def export_storage(comp_map):
    print("\n--- Exporting Storage (Advanced) ---")
    generic_export(oci.core.BlockstorageClient, "list_volumes", comp_map, "storage_block_volumes.csv", {"Name": "display_name", "Size(GB)": "size_in_gbs", "State": "lifecycle_state"})
    generic_export(oci.core.BlockstorageClient, "list_volume_backups", comp_map, "storage_volume_backups.csv", {"Name": "display_name", "Size(GB)": "size_in_gbs", "State": "lifecycle_state"})
    
    # File Storage (Requires AD iteration)
    try:
        fss_client = oci.file_storage.FileStorageClient(config)
        ads = identity_client.list_availability_domains(tenancy_id).data
        fss_list, mt_list = [], []
        
        for comp_id, comp_name in comp_map.items():
            for ad in ads:
                try:
                    file_systems = oci.pagination.list_call_get_all_results(fss_client.list_file_systems, compartment_id=comp_id, availability_domain=ad.name).data
                    for f in file_systems:
                        if hasattr(f, 'lifecycle_state') and f.lifecycle_state in ["DELETED", "DELETING"]: continue
                        fss_list.append({"Compartment": comp_name, "AD": ad.name, "Name": f.display_name, "State": f.lifecycle_state})
                        
                    mount_targets = oci.pagination.list_call_get_all_results(fss_client.list_mount_targets, compartment_id=comp_id, availability_domain=ad.name).data
                    for m in mount_targets:
                        if hasattr(m, 'lifecycle_state') and m.lifecycle_state in ["DELETED", "DELETING"]: continue
                        mt_list.append({"Compartment": comp_name, "AD": ad.name, "Name": m.display_name, "State": m.lifecycle_state})
                except oci.exceptions.ServiceError: pass
        write_csv("storage_fss.csv", ["Compartment", "AD", "Name", "State"], fss_list)
        write_csv("storage_fss_mount_targets.csv", ["Compartment", "AD", "Name", "State"], mt_list)
    except Exception as e:
        print(f"  [!] Failed to extract File Storage: {e}")
    
    # Object Storage (Requires Namespace)
    try:
        os_client = oci.object_storage.ObjectStorageClient(config)
        namespace = os_client.get_namespace().data
        bucket_list = []
        for comp_id, comp_name in comp_map.items():
            try:
                buckets = oci.pagination.list_call_get_all_results(os_client.list_buckets, namespace_name=namespace, compartment_id=comp_id).data
                for b in buckets:
                    bucket_list.append({"Compartment": comp_name, "Name": b.name, "Namespace": b.namespace})
            except oci.exceptions.ServiceError: pass
        write_csv("storage_buckets.csv", ["Compartment", "Name", "Namespace"], bucket_list)
    except Exception as e:
        print(f"  [!] Failed to extract Object Storage: {e}")

def export_databases(comp_map):
    print("\n--- Exporting Databases (Advanced) ---")
    generic_export(oci.database.DatabaseClient, "list_db_systems", comp_map, "db_systems.csv", {"Name": "display_name", "Shape": "shape", "State": "lifecycle_state"})
    generic_export(oci.database.DatabaseClient, "list_autonomous_databases", comp_map, "db_autonomous.csv", {"Name": "display_name", "Workload": "db_workload", "Storage(TB)": "data_storage_size_in_tbs"})
    generic_export(oci.nosql.NosqlClient, "list_tables", comp_map, "db_nosql_tables.csv", {"Name": "name", "State": "lifecycle_state"})
    generic_export(oci.psql.PostgresqlClient, "list_db_systems", comp_map, "db_postgresql.csv", {"Name": "display_name", "State": "lifecycle_state"})
    generic_export(oci.golden_gate.GoldenGateClient, "list_deployments", comp_map, "db_goldengate.csv", {"Name": "display_name", "State": "lifecycle_state"})

def export_loadbalancer(comp_map):
    print("\n--- Exporting Load Balancers ---")
    generic_export(oci.load_balancer.LoadBalancerClient, "list_load_balancers", comp_map, "load_balancers_lbr.csv", {"Name": "display_name", "Shape": "shape_name"})
    generic_export(oci.network_load_balancer.NetworkLoadBalancerClient, "list_network_load_balancers", comp_map, "load_balancers_nlb.csv", {"Name": "display_name"})

def export_management_services(comp_map):
    print("\n--- Exporting Management Services ---")
    generic_export(oci.ons.NotificationControlPlaneClient, "list_topics", comp_map, "mgmt_sns_topics.csv", {"Name": "name", "Description": "description"})
    generic_export(oci.monitoring.MonitoringClient, "list_alarms", comp_map, "mgmt_alarms.csv", {"Name": "display_name", "Severity": "severity"})
    generic_export(oci.logging.LoggingManagementClient, "list_log_groups", comp_map, "mgmt_log_groups.csv", {"Name": "display_name", "State": "lifecycle_state"})
    generic_export(oci.apm_control_plane.ApmDomainClient, "list_apm_domains", comp_map, "mgmt_apm_domains.csv", {"Name": "display_name", "State": "lifecycle_state"})

def export_developer_services(comp_map):
    print("\n--- Exporting Developer Services ---")
    generic_export(oci.container_engine.ContainerEngineClient, "list_clusters", comp_map, "dev_oke_clusters.csv", {"Name": "name", "K8s Version": "kubernetes_version"})
    generic_export(oci.apigateway.GatewayClient, "list_gateways", comp_map, "dev_api_gateways.csv", {"Name": "display_name", "State": "lifecycle_state"})
    generic_export(oci.apigateway.DeploymentClient, "list_deployments", comp_map, "dev_api_deployments.csv", {"Name": "display_name", "State": "lifecycle_state"})
    generic_export(oci.functions.FunctionsManagementClient, "list_applications", comp_map, "dev_functions_apps.csv", {"Name": "display_name", "State": "lifecycle_state"})
    generic_export(oci.streaming.StreamAdminClient, "list_streams", comp_map, "dev_streaming_streams.csv", {"Name": "name", "State": "lifecycle_state"})
    generic_export(oci.resource_manager.ResourceManagerClient, "list_stacks", comp_map, "dev_orm_stacks.csv", {"Name": "display_name", "State": "lifecycle_state"})

def export_security(comp_map):
    print("\n--- Exporting Security (Advanced) ---")
    generic_export(oci.key_management.KmsVaultClient, "list_vaults", comp_map, "sec_kms_vaults.csv", {"Name": "display_name", "Vault Type": "vault_type", "State": "lifecycle_state"})
    generic_export(oci.cloud_guard.CloudGuardClient, "list_targets", comp_map, "sec_cloudguard_targets.csv", {"Name": "display_name", "State": "lifecycle_state", "Target Type": "target_resource_type"})
    generic_export(oci.bastion.BastionClient, "list_bastions", comp_map, "sec_bastions.csv", {"Name": "name", "State": "lifecycle_state", "Bastion Type": "bastion_type"})
    generic_export(oci.waf.WafClient, "list_web_app_firewall_policies", comp_map, "sec_waf_policies.csv", {"Name": "display_name", "State": "lifecycle_state"})
    generic_export(oci.vault.VaultsClient, "list_secrets", comp_map, "sec_vault_secrets.csv", {"Name": "secret_name", "State": "lifecycle_state"})
    generic_export(oci.certificates_management.CertificatesManagementClient, "list_certificates", comp_map, "sec_certificates.csv", {"Name": "name", "State": "lifecycle_state"})

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
    print("      Dependency-Free OCI Resource Exporter")
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
            if not os.listdir(OUT_DIR):
                os.rmdir(OUT_DIR)
            print("Exiting...")
            sys.exit(0)
            
        try:
            choice_int = int(choice)
            if choice_int in MENU_OPTIONS:
                comp_map = get_compartment_map()
                
                MENU_OPTIONS[choice_int][1](comp_map)
                
                zip_path = shutil.make_archive(OUT_DIR, 'zip', OUT_DIR)
                print(f"\n[SUCCESS] Export complete. Files zipped to: {zip_path}")
                break
            else:
                print("Invalid option. Try again.")
        except ValueError:
            print("Invalid input. Please enter a number or 'q'.")

if __name__ == "__main__":
    main()
