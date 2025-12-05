#!/usr/bin/env python
# test_smart_mapping.py
"""
Test script for smart fuzzy matching in API field mapping
Tests various case formats: camelCase, PascalCase, kebab-case, snake_case
"""

def camel_to_snake(name):
    """Convert camelCase or PascalCase to snake_case"""
    import re
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

def normalize_field_name(field_name):
    """Normalize field name for comparison"""
    if not field_name:
        return ""
    # Convert camelCase/PascalCase to snake_case FIRST
    normalized = camel_to_snake(str(field_name))
    # Convert to lowercase
    normalized = normalized.lower()
    # Replace hyphens with underscores
    normalized = normalized.replace('-', '_')
    # Remove special characters but keep underscores
    normalized = ''.join(c if c.isalnum() or c == '_' else '_' for c in normalized)
    # Remove duplicate underscores
    while '__' in normalized:
        normalized = normalized.replace('__', '_')
    # Remove leading/trailing underscores
    normalized = normalized.strip('_')
    return normalized

def calculate_similarity(str1, str2):
    """Calculate similarity score between two strings (0-100)"""
    from difflib import SequenceMatcher
    return SequenceMatcher(None, str1.lower(), str2.lower()).ratio() * 100

# Test cases
test_cases = [
    # (API field, Expected WMS field, Description)
    ("orderId", "order_id", "camelCase → snake_case"),
    ("OrderId", "order_id", "PascalCase → snake_case"),
    ("order-id", "order_id", "kebab-case → snake_case"),
    ("order_id", "order_id", "snake_case → snake_case"),
    ("orderTime", "order_time", "camelCase with Time"),
    ("createdAt", "created_at", "camelCase created"),
    ("createTime", "create_time", "camelCase time variant"),
    ("SKU", "sku", "uppercase"),
    ("itemName", "item_name", "camelCase item"),
    ("productName", "product_name", "camelCase product"),
    ("Quantity", "quantity", "PascalCase quantity"),
    ("qty", "qty", "abbreviated"),
    ("shopName", "shop_name", "camelCase shop"),
    ("logisticType", "logistic_type", "camelCase logistic"),
]

print("=" * 80)
print("TESTING SMART FUZZY MAPPING")
print("=" * 80)
print()

print("📋 Test 1: Case Conversion")
print("-" * 80)
for api_field, expected, description in test_cases:
    normalized = normalize_field_name(api_field)
    normalized_expected = normalize_field_name(expected)
    match = "✅" if normalized == normalized_expected else "❌"
    print(f"{match} {api_field:20} → {normalized:20} ({description})")
print()

print("📋 Test 2: Similarity Matching")
print("-" * 80)
similarity_tests = [
    ("orderTime", "order_time", 70),
    ("createdAt", "order_time", 70),
    ("createTime", "order_time", 70),
    ("orderId", "order_id", 70),
    ("orderNumber", "order_id", 70),
]

for field1, field2, threshold in similarity_tests:
    norm1 = normalize_field_name(field1)
    norm2 = normalize_field_name(field2)
    score = calculate_similarity(norm1, norm2)
    match = "✅" if score >= threshold else "❌"
    print(f"{match} {field1:20} ↔ {field2:20} = {score:5.1f}% (threshold: {threshold}%)")
print()

print("📋 Test 3: Real API Response Simulation")
print("-" * 80)

# Simulate API responses from different platforms
api_responses = [
    {
        "name": "Shopee API",
        "data": {
            "orderId": "SH001",
            "orderTime": "2025-12-04 11:59",
            "SKU": "PROD-001",
            "itemName": "Product A",
            "quantity": 2,
            "shopName": "My Shop"
        }
    },
    {
        "name": "TikTok API",
        "data": {
            "OrderId": "TT001",
            "CreateTime": "2025-12-04 12:00",
            "sku": "PROD-002",
            "title": "Product B",
            "qty": 3,
            "storeName": "TikTok Store"
        }
    },
    {
        "name": "Lazada API",
        "data": {
            "order_sn": "LZ001",
            "created_at": "2025-12-04 13:00",
            "seller_sku": "PROD-003",
            "product_name": "Product C",
            "purchased_qty": 1,
            "shop_name": "Lazada Shop"
        }
    }
]

wms_fields = ['order_id', 'order_time', 'sku', 'item_name', 'qty', 'shop_name']

for api_response in api_responses:
    print(f"\n🔍 {api_response['name']}")
    print("-" * 40)
    api_data = api_response['data']
    api_fields = list(api_data.keys())

    for wms_field in wms_fields:
        normalized_wms = normalize_field_name(wms_field)
        best_match = None
        best_score = 0

        for api_field in api_fields:
            normalized_api = normalize_field_name(api_field)

            # Exact match
            if normalized_api == normalized_wms:
                best_match = api_field
                best_score = 100
                break

            # Fuzzy match
            score = calculate_similarity(normalized_api, normalized_wms)
            if score > best_score and score >= 70:
                best_score = score
                best_match = api_field

        if best_match:
            print(f"  ✅ {wms_field:15} ← {best_match:20} ({best_score:.0f}%)")
        else:
            print(f"  ❌ {wms_field:15} ← No match found")

print()
print("=" * 80)
print("✅ TEST COMPLETED")
print("=" * 80)
