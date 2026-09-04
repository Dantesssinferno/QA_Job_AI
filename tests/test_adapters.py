from qa_job_scout.adapters import enabled_adapters


def test_every_enabled_source_has_a_dedicated_adapter_and_selectors():
    adapters = enabled_adapters()
    assert {adapter.spec.key for adapter in adapters} == {
        "hirehi",
        "rockethunt",
        "dreamjob",
        "hirify",
        "taylor",
        "jobrocket",
        "talanto",
        "getmatch",
        "geekjob",
        "rvc",
        "hhru",
    }
    assert all(
        adapter.spec.card_selector and adapter.spec.link_selector
        for adapter in adapters
    )


def test_source_specific_date_selectors_exist():
    adapters = {
        adapter.spec.key: adapter
        for adapter in enabled_adapters()
    }

    assert ".vacancy-published-date" in adapters["hirehi"].spec.date_selectors
    assert "div.font-light.text-tertiary" in adapters["hirify"].spec.date_selectors
    assert "xpath=//*[@id=\"body\"]/section/article[1]/section/header/div[6]" in adapters["geekjob"].spec.date_selectors


def test_source_vacancy_url_validation_rejects_navigation_links():
    adapters = {
        adapter.spec.key: adapter
        for adapter in enabled_adapters()
    }

    getmatch = adapters["getmatch"]
    assert getmatch.is_valid_vacancy_url(
        "https://getmatch.ru/vacancies/35962-fullstack-qa-inzhener-kreditovanie?s=offers"
    )
    assert not getmatch.is_valid_vacancy_url(
        "https://getmatch.ru/vacancies/qa_manual/moscow?s=vacancies_seo_links_more_vacancies"
    )

    rvc = adapters["rvc"]
    assert rvc.is_valid_vacancy_url(
        "https://app.rvc.global/vacancy/view/luxoft-senior-mobile-manual-qa-engineer-468753"
    )
    assert not rvc.is_valid_vacancy_url(
        "https://app.rvc.global/jobs/remote-jobs-usa"
    )

    rockethunt = adapters["rockethunt"]
    assert rockethunt.is_valid_vacancy_url(
        "https://rockethunt.ai/en/vacancies/a82a65f5-0fd6-4c7a-810d-2f7e60e3b757"
    )

    jobrocket = adapters["jobrocket"]
    assert jobrocket.is_valid_vacancy_url(
        "https://jobrocket.ru/job/qa-engineer-pintopay-454c0d59"
    )


def test_geekjob_validation_rejects_non_vacancy_urls():
    adapter = next(
        adapter
        for adapter in enabled_adapters()
        if adapter.spec.key == "geekjob"
    )

    assert adapter.is_valid_vacancy_url(
        "https://geekjob.ru/vacancy/6a8461a829da6de4890c47b4"
    )
    assert not adapter.is_valid_vacancy_url(
        "https://geekjob.ru/other/123"
    )


def test_dreamjob_detail_url_validation():
    adapter = next(
        adapter
        for adapter in enabled_adapters()
        if adapter.spec.key == "dreamjob"
    )

    assert adapter.is_valid_vacancy_url(
        "https://dreamjob.ru/employers/166387/vakansii/134022696"
    )
    assert not adapter.is_valid_vacancy_url(
        "https://dreamjob.ru/vakansii/vacancy-qa-engineer"
    )


def test_hh_adapter_configuration():
    adapter = next(
        adapter
        for adapter in enabled_adapters()
        if adapter.spec.key == "hhru"
    )

    assert adapter.spec.url == "https://api.hh.ru/vacancies"
    assert adapter.spec.key == "hhru"


def test_hh_remote_detection_from_work_format():
    adapter = next(
        adapter
        for adapter in enabled_adapters()
        if adapter.spec.key == "hhru"
    )

    assert adapter._is_remote(
        {},
        {"work_format": [{"id": "REMOTE", "name": "Из дома"}]},
    )

    assert not adapter._is_remote(
        {},
        {"work_format": [{"id": "ON_SITE", "name": "На месте работодателя"}]},
    )
