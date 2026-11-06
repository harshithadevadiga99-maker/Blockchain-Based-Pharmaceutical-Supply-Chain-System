"""
Predefined valid license numbers for Manufacturer, Supplier, Pharmacy.
Fake/invalid licenses will be rejected at registration.
"""

VALID_LICENSES = {
    # Manufacturers
    'LIC-MFG-001': {'role': 'manufacturer', 'company': 'PharmaCorp Ltd', 'city': 'Bangalore'},
    'LIC-MFG-002': {'role': 'manufacturer', 'company': 'MediLife Pharma', 'city': 'Mumbai'},
    'LIC-MFG-003': {'role': 'manufacturer', 'company': 'HealthGen Biotech', 'city': 'Hyderabad'},
    'LIC-MFG-004': {'role': 'manufacturer', 'company': 'CarePlus Labs', 'city': 'Chennai'},
    'LIC-MFG-005': {'role': 'manufacturer', 'company': 'BioMed Industries', 'city': 'Pune'},

    # Suppliers
    'LIC-SUP-001': {'role': 'supplier', 'company': 'DistribuMed Inc', 'city': 'Delhi'},
    'LIC-SUP-002': {'role': 'supplier', 'company': 'FastMeds Supply', 'city': 'Kolkata'},
    'LIC-SUP-003': {'role': 'supplier', 'company': 'MedTrans Logistics', 'city': 'Ahmedabad'},
    'LIC-SUP-004': {'role': 'supplier', 'company': 'PharmaLink Distributors', 'city': 'Jaipur'},
    'LIC-SUP-005': {'role': 'supplier', 'company': 'SwiftMed Supply', 'city': 'Lucknow'},

    # Pharmacies
    'LIC-PHR-001': {'role': 'pharmacy', 'company': 'City Pharmacy', 'city': 'Bangalore'},
    'LIC-PHR-002': {'role': 'pharmacy', 'company': 'HealthPlus Pharmacy', 'city': 'Mumbai'},
    'LIC-PHR-003': {'role': 'pharmacy', 'company': 'MediCare Pharmacy', 'city': 'Delhi'},
    'LIC-PHR-004': {'role': 'pharmacy', 'company': 'LifeLine Chemists', 'city': 'Chennai'},
    'LIC-PHR-005': {'role': 'pharmacy', 'company': 'Wellness Drug Store', 'city': 'Hyderabad'},
    'LIC-PHR-006': {'role': 'pharmacy', 'company': 'Apollo Pharmacy', 'city': 'Pune'},
    'LIC-PHR-007': {'role': 'pharmacy', 'company': 'MedPlus Pharmacy', 'city': 'Kolkata'},
}


def validate_license(license_number, expected_role):
    """Check if license exists and matches role"""
    if not license_number:
        return False, 'License number is required'
    lic = VALID_LICENSES.get(license_number.upper().strip())
    if not lic:
        return False, f'Invalid license number: {license_number}. Not in official registry.'
    if lic['role'] != expected_role:
        return False, f'License {license_number} is for {lic["role"]}, not {expected_role}'
    return True, lic