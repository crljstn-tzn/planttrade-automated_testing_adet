import json
import os
import random
import time
import urllib.error
import urllib.request

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.edge.options import Options as EdgeOptions


BASE_URL = os.environ.get("BASE_URL", "http://localhost/planttrade2")
BROWSER = os.environ.get("BROWSER", "edge").lower()
HEADLESS = os.environ.get("HEADLESS", "true").lower() != "false"


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
    url = build_app_url("signup.html")
    try:
        with urllib.request.urlopen(url) as response:
            status_code = response.getcode()
    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Unable to reach {BASE_URL}. Start Apache/XAMPP first. Original error: {error}"
        ) from error

    if status_code not in (200, 302):
        raise RuntimeError(f"Expected {BASE_URL} to be reachable, but got HTTP {status_code}.")


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
    # Clear session and browser storage so each test starts clean.
    driver.get(build_app_url("signup.html"))
    api_fetch(driver, "api/logout.php", "POST")
    driver.delete_all_cookies()
    driver.execute_script(
        """
        window.localStorage.clear();
        window.sessionStorage.clear();
        """
    )


def unique_identity(prefix: str = "cart_user") -> dict:
    stamp = f"{int(time.time() * 1000)}-{random.randint(1000, 99999)}"
    return {
        "username": f"{prefix}_{stamp}",
        "email": f"{prefix}_{stamp}@example.com",
        "password": "abcABC123!@#",
    }


def signup_and_login(driver) -> dict:
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


def get_first_cart_ready_product(driver) -> dict:
    response = api_fetch(driver, "api/catalog.php", "GET")
    assert response["status"] == 200, "Expected catalog API to respond successfully."
    assert response["json"]["success"] is True

    catalog_data = response["json"]["data"]
    for _category_key, category in catalog_data.items():
        plants = category.get("plants", {})
        for plant_name, plant in plants.items():
            for variety in plant.get("varieties", []):
                combinations = variety.get("combinations", [])
                if not combinations:
                    continue

                combination = combinations[0]
                return {
                    "productName": variety.get("name") or plant_name,
                    "variation": f"{combination.get('type', '')}, {combination.get('size', '')}",
                    "unitPrice": float(combination.get("price", variety.get("price", 0)) or 0),
                    "inventoryId": int(combination.get("inventoryId", 0) or 0),
                    "img": combination.get("img") or variety.get("img") or "",
                }

    raise RuntimeError("Unable to find a catalog item with a valid inventory combination.")


def save_cart_items(driver, items):
    response = api_fetch(driver, "api/cart.php", "POST", {"items": items})
    assert response["status"] == 200, f"Expected cart save to succeed, got {response['status']}."
    assert response["json"]["success"] is True
    return response


def fetch_cart_items(driver):
    response = api_fetch(driver, "api/cart.php", "GET")
    assert response["status"] == 200, f"Expected cart fetch to succeed, got {response['status']}."
    assert response["json"]["success"] is True
    assert response["json"]["authenticated"] is True
    return response["json"]["items"]


def build_cart_item(product: dict, quantity: int) -> dict:
    # Reuse a real inventory combination so cart tests exercise valid server-side behavior.
    return {
        "productName": product["productName"],
        "variation": product["variation"],
        "unitPrice": product["unitPrice"],
        "quantity": quantity,
        "selected": False,
        "img": product["img"],
        "inventoryId": product["inventoryId"],
    }


def test_duplicate_cart_items_merge(driver):
    signup_and_login(driver)
    product = get_first_cart_ready_product(driver)

    save_cart_items(
        driver,
        [
            build_cart_item(product, 2),
            build_cart_item(product, 3),
        ],
    )

    items = fetch_cart_items(driver)

    assert len(items) == 1, f"Expected 1 merged cart item, but found {len(items)}."
    assert items[0]["inventoryId"] == product["inventoryId"]
    assert items[0]["variation"] == product["variation"]
    assert items[0]["quantity"] == 5, f"Expected merged quantity 5, got {items[0]['quantity']}."


def test_zero_or_negative_quantities_are_discarded(driver):
    signup_and_login(driver)
    product = get_first_cart_ready_product(driver)

    save_cart_items(
        driver,
        [
            build_cart_item(product, 0),
            build_cart_item(product, -2),
        ],
    )

    items = fetch_cart_items(driver)
    assert items == [], f"Expected invalid cart quantities to be discarded, got {items}."


def test_quantity_updates_persist_remotely(driver):
    signup_and_login(driver)
    product = get_first_cart_ready_product(driver)

    save_cart_items(driver, [build_cart_item(product, 1)])
    initial_items = fetch_cart_items(driver)
    assert len(initial_items) == 1
    assert initial_items[0]["quantity"] == 1

    save_cart_items(driver, [build_cart_item(product, 4)])
    updated_items = fetch_cart_items(driver)

    assert len(updated_items) == 1
    assert updated_items[0]["inventoryId"] == product["inventoryId"]
    assert updated_items[0]["quantity"] == 4, (
        f"Expected remote cart quantity to persist as 4, got {updated_items[0]['quantity']}."
    )


TESTS = [
    (
        "Duplicate cart items merge by inventory/variation instead of duplicating incorrectly.",
        test_duplicate_cart_items_merge,
    ),
    (
        "Zero or negative quantity items are discarded.",
        test_zero_or_negative_quantities_are_discarded,
    ),
    (
        "Quantity updates persist remotely for authenticated users.",
        test_quantity_updates_persist_remotely,
    ),
]


def main() -> int:
    ensure_server_reachable()
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

    print(f"\nCompleted {len(TESTS)} customer cart Selenium tests.")
    if failures:
        print(f"Failed: {len(failures)}")
        return 1

    print("All customer cart Selenium tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
