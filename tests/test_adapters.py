from qa_job_scout.adapters import enabled_adapters


def test_every_enabled_source_has_a_dedicated_adapter_and_selectors():
    adapters = enabled_adapters()
    assert {adapter.spec.key for adapter in adapters} == {
        "hirehi", "rockethunt", "dreamjob", "hirify", "taylor", "jobrocket",
        "talanto", "getmatch", "geekjob", "rvc", "linkedin",
    }
    assert all(adapter.spec.card_selector and adapter.spec.link_selector for adapter in adapters)
