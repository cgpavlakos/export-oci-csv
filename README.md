# oci-export
export oci resources to a zipped folder of csvs

A lightweight, dependency-free Python script to export Oracle Cloud Infrastructure (OCI) resource metadata into structured CSV files. Designed for quick audits and data extraction without the overhead of complex automation frameworks.

## Features
* **Declarative Extraction:** Uses a generic extraction engine to pull data uniformly across OCI services.
* **Identity Domain Support:** Natively handles both Legacy IAM (Default Domain) and secondary Identity Domains using SCIM pagination.
* **Zero Clutter:** Automatically filters out `TERMINATED` or `DELETED` resources. Skips generating empty CSV files for services not in use.
* **Automated Archival:** Bundles all generated CSVs into a single timestamped `.zip` archive for easy download.

## Prerequisites: OCI IAM Permissions
To execute a full tenancy export, your OCI user profile must be assigned to a group with, at minimum, tenancy-wide `inspect` permissions.

Create a policy with the following statement:
```text
Allow group <Your-Audit-Group> to inspect all-resources in tenancy
```
*Note: If your execution context encounters compartments where you lack permissions, the script catches the `ServiceError` and skips that specific compartment, proceeding with the rest of the export.*

## Quick Start: OCI Cloud Shell
The most efficient execution path is utilizing the built-in OCI Cloud Shell. It comes pre-configured with the OCI Python SDK and your authentication context, eliminating the need to set up local API keys or config files.

1. Open **Cloud Shell** from the OCI Console.
2. Download the script:
   ```bash
   wget [https://raw.githubusercontent.com/](https://raw.githubusercontent.com/)<YOUR_GITHUB_HANDLE>/<REPO>/main/export-oci.py
   ```
3. Execute the script:
   ```bash
   python3 export-oci.py
   ```
4. Follow the interactive menu to select your target services (or select `1` to export everything).
5. Once completed, click the **Gear Icon** in the Cloud Shell terminal, select **Download**, and enter the generated zip filename (e.g., `oci_export_20260414_163022.zip`).

## Output Structure
The script generates a timestamped working directory (`oci_export_YYYYMMDD_HHMMSS/`) and outputs individual CSV files for each processed resource type. 

### CSV Format
All generated CSVs standardize on the following baseline:
* **Column 1:** `Compartment` (Resolved to the human-readable display name, mapping back to `root` for the tenancy level).
* **Subsequent Columns:** Resource-specific attributes mapped directly from the OCI SDK (e.g., `Name`, `State`, `OCID`, `CIDR`).

Data arrays, such as user group memberships and IAM policy statements, are concatenated using a semicolon delimiter (` ; `). This preserves standard CSV tabular formatting and prevents row breaks in spreadsheet applications like Excel.

Upon successful execution, the script compresses the working directory using standard DEFLATE compression and outputs the `.zip` archive in the root execution directory.
