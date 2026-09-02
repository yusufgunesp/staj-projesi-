"""Netleştirme öbeklerinin testleri.

Gerçek arama koşulmuyor: `facets` yalnızca `_candidates`'in döndürdüğü ham
havuzu okuyor, o yüzden sahte bir havuz yeterli. Ağ ve API anahtarı gerekmiyor.
"""

from __future__ import annotations

import pytest

from codeqa.facets import COVERAGE_CAP, MIN_CHUNKS, Facet, _directory, facets


class FakeSearcher:
    """`facets`'in dokunduğu tek yüzey: `records` ve `_candidates`."""

    def __init__(self, locations: list[str]):
        self.records = [
            {"location": location, "name": location.split("/")[-1].split(":")[0]}
            for location in locations
        ]
        self.calls: list[tuple] = []

    def _candidates(self, query, pool, mode):
        self.calls.append((query, pool, mode))
        return [(index, 1.0, ("vector",)) for index in range(len(self.records))]

    def search(self, *args, **kwargs):  # pragma: no cover - çağrılmamalı
        raise AssertionError("facets sıralamaya dokunmamalı")


def locations(spec: dict[str, int]) -> list[str]:
    """`{"a/b": 3}` → üç ayrı dosya konumu."""
    out = []
    for directory, count in spec.items():
        out += [f"{directory}/f{i}.py:1" for i in range(count)]
    return out


# --- yardımcılar -------------------------------------------------------------


def test_directory_strips_file_and_line():
    assert _directory("saleor/payment/models.py:441") == "saleor/payment"


def test_directory_of_repo_root_file():
    assert _directory("setup.py:1") == "."


# --- ağaç kesme --------------------------------------------------------------


def test_big_branch_is_split_so_subdirectory_surfaces():
    """Ölçülen `ödeme` durumu: `payment` havuzun üçte birini tutuyor ve
    aradığımız ayrım *içinde* duruyor. Kesme olmasaydı `gateways` görünmezdi."""
    searcher = FakeSearcher(locations({"s/payment": 40, "s/payment/gateways": 30, "s/order": 30}))
    found = {facet.directory for facet in facets(searcher, "ödeme")}
    assert "s/payment/gateways" in found
    assert "s/payment" in found


def test_branch_under_cap_stays_whole():
    """Eşiğin altındaki dal bölünmüyor: bölmek netleştirme değil, gürültü."""
    searcher = FakeSearcher(locations({"s/a": 10, "s/a/deep": 5, "s/b": 85}))
    found = {facet.directory for facet in facets(searcher, "x")}
    assert "s/a" in found
    assert "s/a/deep" not in found


def test_chunks_are_counted_once():
    """Bir parça tek öbeğe giriyor; üst dizin altındakileri tekrar saymıyor."""
    searcher = FakeSearcher(locations({"s/payment": 40, "s/payment/gateways": 30, "s/order": 30}))
    found = facets(searcher, "ödeme", limit=99)
    assert sum(facet.chunks for facet in found) == 100


def test_small_directories_are_dropped():
    searcher = FakeSearcher(locations({"s/big": 60, "s/tiny": MIN_CHUNKS - 1, "s/other": 40}))
    found = {facet.directory for facet in facets(searcher, "x")}
    assert "s/tiny" not in found


def test_limit_is_respected():
    searcher = FakeSearcher(locations({f"s/d{i}": 10 for i in range(9)}))
    assert len(facets(searcher, "x", limit=3)) == 3


def test_facets_are_ordered_by_size():
    searcher = FakeSearcher(locations({"s/small": 10, "s/large": 40, "s/mid": 20}))
    found = facets(searcher, "x")
    assert [facet.directory for facet in found][:3] == ["s/large", "s/mid", "s/small"]


def test_empty_pool_returns_nothing():
    assert facets(FakeSearcher([]), "x") == []


# --- sıralamaya dokunmama ----------------------------------------------------


def test_reads_raw_pool_not_ranked_results():
    """`search` çağrılırsa genişletme slotları uygulanır ve liste k'ya kırpılır;
    öbekler tam olarak kırpılan yerde duruyor. `FakeSearcher.search` bu yüzden
    çağrılınca patlıyor."""
    searcher = FakeSearcher(locations({"s/a": 50, "s/b": 50}))
    facets(searcher, "ödeme", mode="vector", pool=250)
    assert searcher.calls == [("ödeme", 250, "vector")]


# --- öbek sorgusu ------------------------------------------------------------


def test_query_keeps_the_original_question():
    facet = Facet(directory="s/payment/gateways", chunks=9, best_rank=15, children=["adyen"])
    assert facet.query("ödeme").startswith("ödeme ")


def test_query_adds_terms_from_the_index():
    facet = Facet(
        directory="s/payment/gateways",
        chunks=9,
        best_rank=15,
        children=["adyen", "braintree"],
    )
    assert facet.query("ödeme") == "ödeme gateways adyen braintree"


def test_query_falls_back_to_symbols_without_children():
    facet = Facet(directory="s/payment", chunks=9, best_rank=1, symbols=["PaymentError"])
    assert facet.query("ödeme") == "ödeme payment PaymentError"


def test_query_does_not_repeat_a_term():
    facet = Facet(directory="s/payment", chunks=9, best_rank=1, children=["payment"])
    assert facet.query("payment") == "payment"


# --- etiket ------------------------------------------------------------------


def test_label_always_shows_directory_and_count():
    """Öneri metni yanlışsa kullanıcı neye baktığını görebilmeli."""
    facet = Facet(directory="s/payment/gateways", chunks=57, best_rank=15, children=["adyen"])
    assert "s/payment/gateways" in facet.label
    assert "57 parça" in facet.label


def test_label_counts_the_children_it_cannot_show():
    facet = Facet(
        directory="s/payment/gateways",
        chunks=57,
        best_rank=15,
        children=["adyen", "braintree", "razorpay", "stripe", "dummy"],
    )
    assert "+2" in facet.label


def test_to_dict_reports_the_full_child_count():
    facet = Facet(directory="s/g", chunks=9, best_rank=2, children=["a", "b", "c", "d"])
    payload = facet.to_dict()
    assert payload["children_total"] == 4
    assert len(payload["children"]) == 3


@pytest.mark.parametrize("cap", [COVERAGE_CAP])
def test_coverage_cap_is_a_fraction(cap):
    assert 0 < cap < 1


# --- ölçülüp reddedilen: dizine kısıtlı tıklama ------------------------------


def test_search_within_filters_to_the_directory():
    """`search_within` üretimde kullanılmıyor (ölçüldü, zarar verdi) ama davranışı
    kayıtlı kalsın: yeniden ölçülecekse aynı şeyin ölçüldüğü bilinsin."""
    from codeqa.facets import search_within

    class Hit:
        def __init__(self, location):
            self.location = location

    class Ranked:
        records = ()

        def search(self, *args, **kwargs):
            return [
                Hit("s/payment/models.py:1"),
                Hit("s/order/models.py:1"),
                Hit("s/payment/gateways/adyen/plugin.py:1"),
            ]

    out = search_within(Ranked(), "ödeme", "s/payment", k=8)
    assert [hit.location for hit in out] == [
        "s/payment/models.py:1",
        "s/payment/gateways/adyen/plugin.py:1",
    ]
