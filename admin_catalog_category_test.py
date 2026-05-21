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


def ensure_admin_account() -> None:
    # Rebuild the expected admin credentials so the API tests can authenticate consistently.
    php_binary = next((candidate for candidate in PHP_CANDIDATES if candidate and os.path.isfile(candidate)), None)
    if php_binary is None:
        raise RuntimeError(
            "Unable to find php.exe. Set the PHP_BIN environment variable or install PHP in "
            "your PATH. Expected locations checked: D:\\xampp\\php\\php.exe and C:\\xampp\\php\\php.exe."
        )

    result = subprocess.run(
        [php_binary, "scripts/ensure_admin_account.php"],
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
    # Clear browser state so each admin scenario starts with a fresh session.
    driver.get(build_app_url("login.html"))
    api_fetch(driver, "api/logout.php", "POST")
    driver.delete_all_cookies()
    driver.execute_script(
        """
        window.localStorage.clear();
        window.sessionStorage.clear();
        """
    )


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
    role = str(response["json"]["user"]["role"]).lower()
    assert role == "admin", f"Expected an admin session, got role '{role}'."


def unique_category_name(prefix: str = "Automation Category") -> str:
    stamp = f"{int(time.time() * 1000)}-{random.randint(1000, 99999)}"
    return f"{prefix} {stamp}"


def fetch_admin_categories(driver):
    response = api_fetch(driver, "api/admin-catalog.php", "GET")
    assert response["status"] == 200, f"Expected admin catalog fetch to succeed, got {response['status']}."
    assert response["json"]["success"] is True
    return response["json"]["catalog"]["categories"]


def find_category_by_name(categories, name: str):
    for category in categories:
        if category.get("name") == name:
            return category
    return None


def save_category(driver, category_id: str, name: str, description: str, profit_margin: float):
    # Use the same categories payload structure the admin catalog page submits.
    response = api_fetch(
        driver,
        "api/admin-catalog.php",
        "POST",
        {
            "action": "save",
            "tab": "categories",
            "id": category_id,
            "values": {
                "name": name,
                "description": description,
                "profitMargin": profit_margin,
            },
        },
    )
    assert response["status"] == 200, f"Expected category save to succeed, got {response['status']}."
    assert response["json"]["success"] is True
    return response["json"]["catalog"]["categories"]


def delete_category(driver, category_id: str):
    response = api_fetch(
        driver,
        "api/admin-catalog.php",
        "POST",
        {
            "action": "delete",
            "tab": "categories",
            "id": category_id,
            "values": {},
        },
    )
    assert response["status"] == 200, f"Expected category delete to succeed, got {response['status']}."
    assert response["json"]["success"] is True
    return response["json"]["catalog"]["categories"]


def create_temp_category(driver) -> dict:
    category_name = unique_category_name()
    description = "Temporary admin category created by Selenium."
    categories = save_category(driver, "", category_name, description, 18)
    created = find_category_by_name(categories, category_name)
    assert created is not None, f"Expected to find the new category '{category_name}'."
    return created


def test_create_category_succeeds(driver):
    login_as_admin(driver)

    category_name = unique_category_name("Created Category")
    description = "Created through admin category automation."
    categories = save_category(driver, "", category_name, description, 15)

    created = find_category_by_name(categories, category_name)
    assert created is not None, "Expected the created category to appear in the admin catalog."
    assert created["description"] == description
    assert float(created["profitMargin"]) == 15.0


def test_update_category_succeeds(driver):
    login_as_admin(driver)
    created = create_temp_category(driver)

    updated_name = created["name"] + " Updated"
    updated_description = "Updated by admin category automation."
    categories = save_category(
        driver,
        created["id"],
        updated_name,
        updated_description,
        22,
    )

    updated = find_category_by_name(categories, updated_name)
    assert updated is not None, "Expected the updated category name to appear in the admin catalog."
    assert updated["id"] == created["id"]
    assert updated["description"] == updated_description
    assert float(updated["profitMargin"]) == 22.0


def test_delete_category_succeeds_when_allowed(driver):
    login_as_admin(driver)
    created = create_temp_category(driver)

    remaining_categories = delete_category(driver, created["id"])
    deleted = find_category_by_name(remaining_categories, created["name"])

    assert deleted is None, "Expected the deleted category to be absent from the active admin catalog."

    refreshed_categories = fetch_admin_categories(driver)
    assert find_category_by_name(refreshed_categories, created["name"]) is None


TESTS = [
    ("Create category succeeds.", test_create_category_succeeds),
    ("Update category succeeds.", test_update_category_succeeds),
    (
        "Delete category succeeds when allowed by DB constraints.",
        test_delete_category_succeeds_when_allowed,
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

    print(f"\nCompleted {len(TESTS)} admin catalog category Selenium tests.")
    if failures:
        print(f"Failed: {len(failures)}")
        return 1

    print("All admin catalog category Selenium tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
