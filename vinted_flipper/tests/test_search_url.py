import pytest
from vinted_flipper.vinted.search_url import InvalidSearchUrl, parse_search_url


def test_filters_are_preserved_and_paged():
    query = parse_search_url(
        "https://www.vinted.fr/catalog?search_text=ralph+lauren&brand_ids[]=88"
        "&size_ids[]=207&status_ids[]=6&price_to=20"
    )
    assert query.params["brand_ids[]"] == ["88"]
    assert query.params["price_to"] == ["20"]
    url = query.page_url(3)
    assert "page=3" in url and "brand_ids%5B%5D=88" in url


def test_category_in_path_is_picked_up():
    query = parse_search_url("https://www.vinted.fr/catalog/1805-shirts?brand_ids[]=88")
    assert query.params["catalog[]"] == ["1805"]


def test_existing_page_number_is_dropped():
    query = parse_search_url("https://www.vinted.fr/catalog?search_text=nike&page=7")
    assert "page=1" in query.page_url(1)
    assert query.page_url(1).count("page=") == 1


def test_describe_translates_condition_codes():
    query = parse_search_url("https://www.vinted.fr/catalog?search_text=nike&status_ids[]=6")
    described = query.describe()
    assert described["Recherche"] == "nike"
    assert described["États"] == "Neuf avec étiquette"


def test_other_vinted_domains_are_accepted():
    assert parse_search_url("https://www.vinted.be/catalog?search_text=nike").domain == (
        "www.vinted.be"
    )


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://www.leboncoin.fr/recherche?q=nike",
        "https://www.vinted.fr/items/123-pull",
        "https://www.vinted.fr/catalog",
    ],
)
def test_bad_urls_are_rejected_with_an_explanation(url):
    with pytest.raises(InvalidSearchUrl):
        parse_search_url(url)


def test_suggested_name_is_readable_not_a_dump_of_ids():
    query = parse_search_url(
        "https://www.vinted.fr/catalog?search_text=ralph+lauren&brand_ids[]=88"
        "&status_ids[]=6&price_to=30&order=newest_first"
    )
    name = query.suggested_name()
    assert name == "ralph lauren · Neuf avec étiquette · ≤ 30 €"
    assert "88" not in name and "newest_first" not in name


def test_suggested_name_falls_back_to_counting_filters():
    query = parse_search_url("https://www.vinted.fr/catalog?brand_ids[]=88&brand_ids[]=53")
    assert query.suggested_name() == "2 marques"
