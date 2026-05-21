import json
import os
import random
import time
import urllib.error
import urllib.request
from pathlib import Path

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
    # Use Edge by default on this Windows/XAMPP setup, but keep Chrome optional.
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
    # Reset session and browser storage between tests so scenarios stay independent.
    driver.get(build_app_url("signup.html"))
    api_fetch(driver, "api/logout.php", "POST")
    driver.delete_all_cookies()
    driver.execute_script(
        """
        window.localStorage.clear();
        window.sessionStorage.clear();
        """
    )


def unique_identity(prefix: str = "signup") -> dict:
    stamp = f"{int(time.time() * 1000)}-{random.randint(1000, 99999)}"
    return {
        "username": f"{prefix}_{stamp}",
        "email": f"{prefix}_{stamp}@example.com",
        "password": "abcABC123!@#",
    }


def signup_via_api(driver, overrides=None):
    overrides = overrides or {}
    identity = unique_identity("signup")
    identity.update(overrides)

    response = api_fetch(
        driver,
        "api/signup.php",
        "POST",
        {
            "username": identity["username"],
            "email": identity["email"],
            "password": identity["password"],
            "confirmPassword": overrides.get("confirmPassword", identity["password"]),
        },
    )
    return identity, response


def test_signup_succeeds(driver):
    driver.get(build_app_url("signup.html"))
    identity, response = signup_via_api(driver)

    assert response["status"] == 201
    assert response["json"]["success"] is True
    assert response["json"]["user"]["username"] == identity["username"]
    assert response["json"]["user"]["email"] == identity["email"]


def test_signup_fails_when_required_field_is_empty(driver):
    raise NotImplementedError


def test_signup_fails_when_passwords_do_not_match(driver):
    driver.get(build_app_url("signup.html"))
    _, response = signup_via_api(driver, {"confirmPassword": "differentPassword123!"})

    assert response["status"] == 422
    assert response["json"]["success"] is False
    assert "Passwords do not match." in response["json"]["message"]


def test_signup_fails_when_password_is_short(driver):
    raise NotImplementedError


def test_signup_fails_when_email_exists(driver):
    driver.get(build_app_url("signup.html"))
    existing = unique_identity("duplicate_email")
    _, first_response = signup_via_api(driver, existing)
    assert first_response["status"] == 201

    reset_browser_state(driver)
    driver.get(build_app_url("signup.html"))

    _, second_response = signup_via_api(
        driver,
        {
            "username": unique_identity("different_user")["username"],
            "email": existing["email"],
        },
    )

    assert second_response["status"] == 409
    assert second_response["json"]["success"] is False
    assert "already registered" in second_response["json"]["message"].lower()


TESTS = [
    ("Sign up succeeds with valid username, email, password, and confirm password.", test_signup_succeeds),
    ("Sign up fails when password and confirm password do not match.", test_signup_fails_when_passwords_do_not_match),
    ("Sign up fails when email already exists.", test_signup_fails_when_email_exists),
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
            except Exception as error:  # Keep the runner simple and readable.
                failures.append((test_name, error))
                print(f"[FAIL] {test_name}")
                print(str(error))
    finally:
        driver.quit()

    print(f"\nCompleted {len(TESTS)} signup Selenium tests.")
    if failures:
      print(f"Failed: {len(failures)}")
      return 1

    print("All signup Selenium tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
