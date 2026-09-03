from qa_job_scout.adapters import enabled_adapters


def test_every_enabled_source_has_a_dedicated_adapter_and_selectors():
    adapters = enabled_adapters()
    assert {adapter.spec.key for adapter in adapters} == {
        "hirehi", "rockethunt", "dreamjob", "hirify", "taylor", "jobrocket",
        "talanto", "getmatch", "geekjob", "rvc", "linkedin",
    }
    assert all(adapter.spec.card_selector and adapter.spec.link_selector for adapter in adapters)


def test_source_specific_date_selectors_exist():
    adapters = {adapter.spec.key: adapter for adapter in enabled_adapters()}

    assert ".vacancy-published-date" in adapters["hirehi"].spec.date_selectors
    assert "div.font-light.text-tertiary" in adapters["hirify"].spec.date_selectors


def test_problematic_sources_have_fallback_link_selectors():
    adapters = {adapter.spec.key: adapter for adapter in enabled_adapters()}

    assert "a[href*='/jobs/']" in adapters["rvc"].spec.link_selector
    assert "a[href*='/job/']" in adapters["rockethunt"].spec.link_selector
