import requests
import time
import os
import sys

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_URL = "http://127.0.0.1:8000"

def test_auth_isolation():
    print("=================================================================")
    print("🔒 TESTING CUSTOMER AUTHENTICATION & MULTI-TENANT LOCAL ISOLATION")
    print("=================================================================")

    # 1. Login Customer 1
    print("\n[1] Logging in as Customer 1 (Apex Retailers)...")
    r1 = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": "customer1@storehub.io",
        "password": "Password123!"
    })
    assert r1.status_code == 200, f"Customer 1 login failed: {r1.text}"
    c1_data = r1.json()
    token1 = c1_data["token"]
    user1_id = c1_data["user"]["id"]
    headers1 = {"Authorization": f"Bearer {token1}"}
    print(f"    ✅ Logged in Customer 1: {c1_data['user']['name']} (ID: {user1_id})")

    # 2. Login Customer 2
    print("\n[2] Logging in as Customer 2 (Nova Brands)...")
    r2 = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": "customer2@storehub.io",
        "password": "Password123!"
    })
    assert r2.status_code == 200, f"Customer 2 login failed: {r2.text}"
    c2_data = r2.json()
    token2 = c2_data["token"]
    user2_id = c2_data["user"]["id"]
    headers2 = {"Authorization": f"Bearer {token2}"}
    print(f"    ✅ Logged in Customer 2: {c2_data['user']['name']} (ID: {user2_id})")

    # 3. Customer 1 creates a private project
    print("\n[3] Customer 1 creates private project 'Customer 1 Confidential Catalog'...")
    r = requests.post(f"{BASE_URL}/api/projects", json={
        "name": "Customer 1 Confidential Catalog",
        "platform": "amazon"
    }, headers=headers1)
    assert r.status_code == 200
    p1 = r.json()["project"]
    p1_id = p1["id"]
    print(f"    ✅ Created Project: {p1['name']} (ID: {p1_id})")

    # Customer 1 adds confidential products
    r = requests.post(f"{BASE_URL}/api/projects/{p1_id}/manual-urls", json={
        "urls_or_names": "Customer 1 Secret Product A\nCustomer 1 Secret Product B",
        "column_name": "Product Name"
    }, headers=headers1)
    assert r.status_code == 200
    print("    ✅ Imported 2 confidential items into Customer 1 workspace.")

    # 4. Customer 2 lists projects
    print("\n[4] Customer 2 queries project inventory...")
    r = requests.get(f"{BASE_URL}/api/projects", headers=headers2)
    assert r.status_code == 200
    c2_projects = r.json()
    print(f"    Customer 2 sees {len(c2_projects)} projects.")
    c2_project_ids = [p["id"] for p in c2_projects]
    assert p1_id not in c2_project_ids, "CRITICAL ERROR: Customer 1 project leaked into Customer 2 inventory!"
    print(f"    ✅ VERIFIED: Customer 1's project {p1_id} is completely INVISIBLE to Customer 2.")

    # 5. Customer 2 tries to directly access Customer 1's project ID
    print("\n[5] Customer 2 attempts direct access to Customer 1's project...")
    r = requests.get(f"{BASE_URL}/api/projects/{p1_id}", headers=headers2)
    print(f"    Customer 2 direct access response code: {r.status_code}")
    assert r.status_code == 404, f"Expected 404 Not Found, got {r.status_code}"
    print("    ✅ VERIFIED: Direct cross-tenant access blocked with 404 Not Found.")

    # 6. Customer 2 creates their own project
    print("\n[6] Customer 2 creates their own project 'Customer 2 Public Goods'...")
    r = requests.post(f"{BASE_URL}/api/projects", json={
        "name": "Customer 2 Public Goods",
        "platform": "amazon"
    }, headers=headers2)
    assert r.status_code == 200
    p2 = r.json()["project"]
    p2_id = p2["id"]
    print(f"    ✅ Customer 2 created: {p2['name']} (ID: {p2_id})")

    # 7. Customer 1 queries projects
    print("\n[7] Customer 1 queries project inventory...")
    r = requests.get(f"{BASE_URL}/api/projects", headers=headers1)
    assert r.status_code == 200
    c1_projects = r.json()
    c1_project_ids = [p["id"] for p in c1_projects]
    assert p2_id not in c1_project_ids, "CRITICAL ERROR: Customer 2 project leaked into Customer 1 inventory!"
    assert p1_id in c1_project_ids, "Customer 1's own project is missing!"
    print(f"    ✅ VERIFIED: Customer 1 sees only Customer 1's projects. Customer 2's project {p2_id} is INVISIBLE to Customer 1.")

    # 8. Physical disk verification of isolated local storage
    print("\n[8] Inspecting physical local storage directories...")
    studio_user_data = "e:\\shopify stock\\studio\\user_data"
    c1_dir = os.path.join(studio_user_data, user1_id)
    c2_dir = os.path.join(studio_user_data, user2_id)
    print(f"    Customer 1 physical storage vault: {c1_dir} (Exists: {os.path.exists(c1_dir)})")
    print(f"    Customer 2 physical storage vault: {c2_dir} (Exists: {os.path.exists(c2_dir)})")
    assert os.path.exists(os.path.join(c1_dir, "projects.db")), "Customer 1 local db missing"
    assert os.path.exists(os.path.join(c2_dir, "projects.db")), "Customer 2 local db missing"
    print("    ✅ VERIFIED: Customer 1 and Customer 2 have separate physical database files on disk.")

    print("\n=================================================================")
    print("🎉 ALL CUSTOMER ISOLATION & AUTHENTICATION CHECKS PASSED 100%!")
    print("=================================================================")

if __name__ == "__main__":
    test_auth_isolation()
