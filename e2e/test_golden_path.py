import re

from playwright.sync_api import Page, expect


def _register(page: Page, base_url: str, email: str, password: str = "testpassword1") -> None:
    page.goto(f"{base_url}/register")
    page.fill('input[name="email"]', email)
    page.fill('input[name="password"]', password)
    page.fill('input[name="password_confirm"]', password)
    page.click('form.settings-form button[type="submit"]')
    page.wait_for_url(f"{base_url}/")


def test_register_add_airport_and_configure_quiet_hours(live_server, page: Page):
    """Exercises the actual golden path through a real browser: register,
    add an airport via the HTMX-driven live search, then configure and save
    quiet hours - the same flow a new user would follow by hand.
    """
    _register(page, live_server, "e2e-golden@example.com")
    expect(page.locator("h1")).to_have_text("Sichtungs-Feed")

    page.goto(f"{live_server}/airports")
    search_box = page.locator('input[name="q"]')
    search_box.click()
    search_box.press_sequentially("Frankfurt", delay=30)
    page.wait_for_selector("#search-results .item-row", timeout=5000)
    page.locator("#search-results form button[type=submit]").first.click()
    page.wait_for_url(re.compile(r".*/airports\?added=1$"))
    expect(page.locator(".flash")).to_have_text("Flughafen hinzugefügt.")
    expect(page.locator(".item-row .airport-tag").first).to_be_visible()

    page.goto(f"{live_server}/settings")
    page.check('input[name="quiet_hours_enabled"]')
    page.fill('input[name="quiet_hours_start"]', "22:00")
    page.fill('input[name="quiet_hours_end"]', "07:00")
    page.locator("#quiet_hours button[type=submit]").click()
    page.wait_for_url(re.compile(r".*/settings\?saved=quiet_hours"))

    expect(page.locator('input[name="quiet_hours_enabled"]')).to_be_checked()
    expect(page.locator('input[name="quiet_hours_start"]')).to_have_value("22:00")
    expect(page.locator('input[name="quiet_hours_end"]')).to_have_value("07:00")


def test_quiet_hours_time_inputs_use_the_dark_theme_background(live_server, page: Page):
    """Regression test for the exact bug that motivated this whole suite:
    the settings-form dark-input CSS rule listed specific input types and
    missed type="time", so the quiet-hours fields rendered with the
    browser's default white background instead of the app's dark theme.
    Unlike the unit/integration test suite, this checks the actual rendered,
    computed style in a real browser - the only way to catch a purely
    visual regression like this one.
    """
    _register(page, live_server, "e2e-css@example.com")
    page.goto(f"{live_server}/settings")

    background_color = page.eval_on_selector(
        'input[name="quiet_hours_start"]', "el => getComputedStyle(el).backgroundColor"
    )
    # rgb(16, 20, 28) is --bg (#10141c) from app/static/style.css - not white
    # and not the browser's default transparent/white form-control background.
    assert background_color == "rgb(16, 20, 28)"
