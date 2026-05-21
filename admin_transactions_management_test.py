import os
import random
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.edge.options import Options as EdgeOptions


BASE_URL = os.environ.get("BASE_URL", "http://localhost/planttrade2")
BROWSER = os.environ.get("BROWSER", "edge").lower()
HEADLESS = os.environ.get("HEADLESS", "true").lower() != "false"
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ADMIN_EMAIL = "admin@gmail.com"
ADMIN_PASSWORD = "abcABC123!@#"
PHP_CANDIDATES = [
    os.environ.get("PHP_BIN", ""),
    shutil.which("php") or "",
    r"D:\xampp\php\php.exe",
    r"C:\xampp\php\php.exe",
]


def build_app_url(pathname: str) -> str:
    base = BASE_URL if BASE_URL.endswith("/") else f"{BASE_URL}/"
    return f"{base}{pathname}"


def build_driver():
    # Keep Edge as the default browser for this Windows/XAMPP setup.
    if BROWSER == "chrome":
        options = ChromeOptions()
        if HEADLESS:
            options.add_argument("--headless=new")
        options.add_argument("--window-size=1440,1100")
        return webdriver.Chrome(options=options)

    options = EdgeOptions()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1440,1100")
    return webdriver.Edge(options=options)


def ensure_server_reachable() -> None:
    url = build_app_url("login.html")
    try:
        with urllib.request.urlopen(url) as response:
            status_code = response.getcode()
    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Unable to reach {BASE_URL}. Start Apache/XAMPP first. Original error: {error}"
        ) from error

    if status_code not in (200, 302):
        raise RuntimeError(f"Expected {BASE_URL} to be reachable, but got HTTP {status_code}.")


def find_php_binary() -> str:
    php_binary = next((candidate for candidate in PHP_CANDIDATES if candidate and os.path.isfile(candidate)), None)
    if php_binary is None:
        raise RuntimeError(
            "Unable to find php.exe. Set the PHP_BIN environment variable or install PHP in "
            "your PATH. Expected locations checked: D:\\xampp\\php\\php.exe and C:\\xampp\\php\\php.exe."
        )
    return php_binary


def ensure_admin_account() -> None:
    result = subprocess.run(
        [find_php_binary(), "scripts/ensure_admin_account.php"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Unable to prepare the admin account. "
            f"stdout: {result.stdout.strip()} stderr: {result.stderr.strip()}"
        )


def api_fetch(driver, pathname: str, method: str = "GET", body=None):
    url = build_app_url(pathname)

    script = """
        const [requestUrl, requestMethod, requestBody, done] = arguments;
        const fetchOptions = {
          method: requestMethod,
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        };

        if (requestBody !== null) {
          fetchOptions.headers["Content-Type"] = "application/json";
          fetchOptions.body = JSON.stringify(requestBody);
        }

        fetch(requestUrl, fetchOptions)
          .then(async (response) => {
            const rawText = await response.text();
            let parsed = null;

            try {
              parsed = rawText ? JSON.parse(rawText) : null;
            } catch (error) {
              parsed = null;
            }

            done({
              ok: response.ok,
              status: response.status,
              url: response.url,
              text: rawText,
              json: parsed,
            });
          })
          .catch((error) => {
            done({
              ok: false,
              status: 0,
              url: requestUrl,
              text: String(error && error.message ? error.message : error),
              json: null,
            });
          });
    """

    return driver.execute_async_script(script, url, method, body)


def reset_browser_state(driver) -> None:
    # Clear session and browser storage so each scenario runs independently.
    driver.get(build_app_url("login.html"))
    api_fetch(driver, "api/logout.php", "POST")
    driver.delete_all_cookies()
    driver.execute_script(
        """
        window.localStorage.clear();
        window.sessionStorage.clear();
        """
    )


def unique_identity(prefix: str = "txn_user") -> dict:
    stamp = f"{int(time.time() * 1000)}-{random.randint(1000, 99999)}"
    return {
        "username": f"{prefix}_{stamp}",
        "email": f"{prefix}_{stamp}@example.com",
        "password": "abcABC123!@#",
    }


def signup_customer(driver) -> dict:
    driver.get(build_app_url("signup.html"))
    identity = unique_identity()
    response = api_fetch(
        driver,
        "api/signup.php",
        "POST",
        {
            "username": identity["username"],
            "email": identity["email"],
            "password": identity["password"],
            "confirmPassword": identity["password"],
        },
    )
    assert response["status"] == 201, f"Expected signup to succeed, got {response['status']}."
    assert response["json"]["success"] is True
    return identity


def login_as_admin(driver) -> None:
    driver.get(build_app_url("login.html"))
    response = api_fetch(
        driver,
        "api/login.php",
        "POST",
        {
            "identifier": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
        },
    )

    assert response["status"] == 200, f"Expected admin login to succeed, got {response['status']}."
    assert response["json"]["success"] is True
    assert str(response["json"]["user"]["role"]).lower() == "admin"


def update_customer_profile(driver, identity: dict, full_name: str) -> None:
    response = api_fetch(
        driver,
        "api/profile.php",
        "POST",
        {
            "username": identity["username"],
            "name": full_name,
            "email": identity["email"],
            "address": "123 Garden St, Barangay Green, Davao City, Davao del Sur",
            "phone": "09171234567",
            "password": "",
        },
    )
    assert response["status"] == 200, f"Expected profile update to succeed, got {response['status']}."
    assert response["json"]["success"] is True


def get_first_order_ready_product(driver) -> dict:
    response = api_fetch(driver, "api/catalog.php", "GET")
    assert response["status"] == 200, "Expected catalog API to respond successfully."
    assert response["json"]["success"] is True

    catalog_data = response["json"]["data"]
    for _category_key, category in catalog_data.items():
        for plant_name, plant in category.get("plants", {}).items():
            for variety in plant.get("varieties", []):
                combinations = variety.get("combinations", [])
                if not combinations:
                    continue

                combination = combinations[0]
                return {
                    "productName": variety.get("name") or plant_name,
                    "type": combination.get("type", ""),
                    "size": combination.get("size", ""),
                    "inventoryId": int(combination.get("inventoryId", 0) or 0),
                    "quantity": 1,
                }

    raise RuntimeError("Unable to find a catalog item with a valid inventory combination.")


def create_customer_order(driver, full_name: str) -> dict:
    identity = signup_customer(driver)
    update_customer_profile(driver, identity, full_name)
    product = get_first_order_ready_product(driver)

    response = api_fetch(
        driver,
        "api/orders.php",
        "POST",
        {
            "action": "create",
            "paymentMethod": "COD",
            "items": [product],
        },
    )
    assert response["status"] == 200, f"Expected order creation to succeed, got {response['status']}."
    assert response["json"]["success"] is True

    order_id = int(response["json"]["orderId"])
    orders_response = api_fetch(driver, "api/orders.php", "GET")
    assert orders_response["status"] == 200
    order = next((item for item in orders_response["json"]["orders"] if int(item["dbId"]) == order_id), None)
    assert order is not None, f"Expected the new order {order_id} to appear in purchase history."

    return {
        "identity": identity,
        "orderId": order_id,
        "purchaseOrder": order,
    }


def fetch_admin_transaction(driver, order_id: int) -> dict:
    response = api_fetch(driver, "api/admin-transactions.php", "GET")
    assert response["status"] == 200, f"Expected admin transactions fetch to succeed, got {response['status']}."
    assert response["json"]["success"] is True
    transaction = next(
        (item for item in response["json"]["transactions"] if int(item["dbId"]) == int(order_id)),
        None,
    )
    assert transaction is not None, f"Expected to find transaction for order {order_id}."
    return transaction


def edit_admin_transaction(driver, transaction: dict, name: str, status: str = "Completed") -> dict:
    response = api_fetch(
        driver,
        "api/admin-transactions.php",
        "POST",
        {
            "action": "edit",
            "dbId": transaction["dbId"],
            "date": transaction["date"],
            "name": name,
            "payment": transaction["payment"],
            "status": status,
            "contact": transaction["contact"],
            "address": transaction["address"],
            "amount": transaction["amount"],
        },
    )
    assert response["status"] == 200, f"Expected transaction edit to succeed, got {response['status']}."
    assert response["json"]["success"] is True
    updated = next(
        (item for item in response["json"]["transactions"] if int(item["dbId"]) == int(transaction["dbId"])),
        None,
    )
    assert updated is not None
    return updated


def update_admin_transaction_status(driver, order_id: int, status: str) -> dict:
    response = api_fetch(
        driver,
        "api/admin-transactions.php",
        "POST",
        {
            "action": "update_status",
            "dbId": order_id,
            "status": status,
        },
    )
    assert response["status"] == 200, f"Expected transaction status update to succeed, got {response['status']}."
    assert response["json"]["success"] is True
    updated = next(
        (item for item in response["json"]["transactions"] if int(item["dbId"]) == int(order_id)),
        None,
    )
    assert updated is not None
    return updated


def test_edit_transaction_updates_customer_name(driver):
    created = create_customer_order(driver, "Original Customer")

    reset_browser_state(driver)
    login_as_admin(driver)
    transaction = fetch_admin_transaction(driver, created["orderId"])
    updated = edit_admin_transaction(driver, transaction, "Updated Customer Name", "Completed")

    assert updated["name"] == "Updated Customer Name", (
        f"Expected edited customer name to be 'Updated Customer Name', got '{updated['name']}'."
    )


def test_update_status_changes_delivery_status(driver):
    created = create_customer_order(driver, "Status Candidate")

    reset_browser_state(driver)
    login_as_admin(driver)
    updated = update_admin_transaction_status(driver, created["orderId"], "Out for Delivery")

    assert updated["status"] == "Out for Delivery", (
        f"Expected updated delivery status to be 'Out for Delivery', got '{updated['status']}'."
    )

    refreshed = fetch_admin_transaction(driver, created["orderId"])
    assert refreshed["status"] == "Out for Delivery"


def test_purchase_history_shows_expected_fields(driver):
    created = create_customer_order(driver, "History Customer")
    order = created["purchaseOrder"]

    assert str(order.get("invoiceNumber", "")).strip() != "", "Expected purchase history to show an invoice number."
    assert str(order.get("date", "")).strip() != "", "Expected purchase history to show an order date."
    assert str(order.get("deliveryDate", "")).strip() != "", "Expected purchase history to show a delivery date."
    assert float(order.get("amount", 0)) > 0, "Expected purchase history total amount to be greater than zero."
    assert isinstance(order.get("items"), list) and order["items"], "Expected purchase history to include ordered items."


TESTS = [
    ("Edit transaction updates customer name.", test_edit_transaction_updates_customer_name),
    ("Update status changes delivery status in DB.", test_update_status_changes_delivery_status),
    (
        "Purchase history shows invoice number, order date, delivery date, total, items.",
        test_purchase_history_shows_expected_fields,
    ),
]


def main() -> int:
    ensure_server_reachable()
    ensure_admin_account()
    driver = build_driver()
    failures = []

    try:
        for test_name, test_function in TESTS:
            print(f"\n[RUN ] {test_name}")
            try:
                reset_browser_state(driver)
                test_function(driver)
                print(f"[PASS] {test_name}")
            except Exception as error:
                failures.append((test_name, error))
                print(f"[FAIL] {test_name}")
                print(str(error))
    finally:
        driver.quit()

    print(f"\nCompleted {len(TESTS)} admin transactions Selenium tests.")
    if failures:
        print(f"Failed: {len(failures)}")
        return 1

    print("All admin transactions Selenium tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
