import oci
import csv
import sys

# Initialize OCI Config
try:
    config = oci.config.from_file()
    identity = oci.identity.IdentityClient(config)
    tenancy_id = config["tenancy"]
except Exception as e:
    print(f"Error loading OCI config: {e}")
    sys.exit(1)

def get_compartment_map():
    """Returns a map of {ocid: name} including root."""
    name_map = {tenancy_id: "root"}
    comps = oci.pagination.list_call_get_all_results(
        identity.list_compartments, tenancy_id, 
        compartment_id_in_subtree=True, access_level="ANY"
    ).data
    for c in comps:
        name_map[c.id] = c.name
    return name_map

def get_group_map():
    """Returns a map of {ocid: name}."""
    groups = oci.pagination.list_call_get_all_results(identity.list_groups, tenancy_id).data
    return {g.id: g.name for g in groups}

def export_users(comp_map, group_map):
    print("Exporting Users and Group Memberships...")
    users = oci.pagination.list_call_get_all_results(identity.list_users, tenancy_id).data
    user_list = []
    
    for u in users:
        # Fetch memberships for each specific user
        memberships = oci.pagination.list_call_get_all_results(
            identity.list_user_group_memberships, tenancy_id, user_id=u.id
        ).data
        
        user_groups = [group_map.get(m.group_id, "Unknown Group") for m in memberships]
        
        user_list.append({
            "Name": u.name,
            "Email": u.email,
            "OCID": u.id,
            "Groups": " ; ".join(user_groups)
        })
    
    write_csv("identity_users.csv", ["Name", "Email", "OCID", "Groups"], user_list)

def export_groups():
    print("Exporting Groups...")
    groups = oci.pagination.list_call_get_all_results(identity.list_groups, tenancy_id).data
    group_list = [{"Name": g.name, "Description": g.description, "OCID": g.id} for g in groups]
    write_csv("identity_groups.csv", ["Name", "Description", "OCID"], group_list)

def export_policies(comp_map):
    print("Exporting Policies from all compartments...")
    policy_list = []
    
    # Iterate through all compartments because list_policies doesn't support subtree recursion
    for comp_id, comp_name in comp_map.items():
        try:
            policies = oci.pagination.list_call_get_all_results(
                identity.list_policies, comp_id
            ).data
            for p in policies:
                policy_list.append({
                    "Name": p.name,
                    "Compartment Name": comp_name,
                    "Description": p.description,
                    "Policy Statements": " ; ".join(p.statements)
                })
        except oci.exceptions.ServiceError:
            # Skips compartments where you may lack read permissions
            continue
            
    write_csv("identity_policies.csv", ["Name", "Compartment Name", "Description", "Policy Statements"], policy_list)

def export_compartments(comp_map):
    print("Exporting Compartments...")
    comp_list = [{"Name": name, "OCID": ocid} for ocid, name in comp_map.items()]
    write_csv("identity_compartments.csv", ["Name", "OCID"], comp_list)

def write_csv(filename, headers, data):
    with open(filename, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(data)
    print(f"  Successfully wrote {len(data)} rows to {filename}")

if __name__ == "__main__":
    # Pre-fetch maps to avoid redundant API calls
    c_map = get_compartment_map()
    g_map = get_group_map()
    
    export_users(c_map, g_map)
    export_groups()
    export_policies(c_map)
    export_compartments(c_map)
    
    print("\nAll identity exports completed.")
