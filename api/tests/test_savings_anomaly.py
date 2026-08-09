"""Price anomalies and batch-buy advice.

Prices in this system are optional and typed by hand, so the population is small and ragged.
That shapes both rules under test: stay silent until there is enough history to say anything
(`MIN_OBSERVATIONS`), and never recommend buying more of something the household won't get
through — "buy the big pack" is advice to waste more, which would be embarrassing in a
food-waste app.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.pricing import PriceObservation
from app.services.savings import anomaly


def observe(db, user, product, price, *, store="Coop", days_ago=0, is_demo=False):
    row = PriceObservation(
        user_id=user.id,
        product_id=product.id,
        store=store,
        unit_price_eur=Decimal(str(price)),
        unit=product.default_unit,
        observed_at=date.today() - timedelta(days=days_ago),
    )
    db.add(row)
    db.flush()
    return row


# Small, deterministic jitter around the base price, as a percentage. Real price histories
# wobble; a run of *identical* prices has a robust sigma of zero and is deliberately silent
# (see `test_an_unvarying_history_does_not_divide_by_zero`), so a fixture built from
# identical prices would test that branch by accident instead of the detector.
_JITTER_PCT = [0.0, +2.5, -2.5, +1.0, -1.5, +0.5, -1.0, +2.0]


def steady_history(db, user, product, price="2.00", n=6, *, store="Coop"):
    """`n` slightly varying prices around `price`, oldest first, ending yesterday."""
    base = Decimal(str(price))
    for i in range(n):
        pct = Decimal(str(_JITTER_PCT[i % len(_JITTER_PCT)]))
        value = (base * (Decimal("100") + pct) / Decimal("100")).quantize(Decimal("0.01"))
        observe(db, user, product, value, store=store, days_ago=n - i)


class TestSilenceBelowThreshold:
    @pytest.mark.parametrize("n", [0, 1, 2, 3, 4])
    def test_no_claim_is_made_from_too_little_history(self, db, seeded, n):
        # A "1.5 sigma deviation" computed from two data points is not a finding, it is
        # noise with a decimal point.
        user, product = seeded["user"], seeded["mozzarella"]
        for i in range(n):
            observe(db, user, product, "2.00", days_ago=n - i)
        observe(db, user, product, "9.99")  # wildly out of line
        db.commit()

        anomalies, _, _ = anomaly.price_anomalies(db, user_id=user.id)
        assert anomalies == []

    def test_the_report_says_how_many_pairs_were_withheld(self, db, seeded):
        # An empty anomaly list means something very different at 0 pairs than at 40.
        user, product = seeded["user"], seeded["mozzarella"]
        observe(db, user, product, "2.00", days_ago=1)
        observe(db, user, product, "2.10")
        db.commit()

        report = anomaly.report(db, user_id=user.id)
        assert report.pairs_examined == 1
        assert report.pairs_below_threshold == 1
        assert report.anomalies == []
        assert report.notes  # explains itself rather than looking like "nothing is wrong"


class TestAnomalyDetection:
    def test_a_clear_outlier_above_the_trailing_median_fires(self, db, seeded):
        user, product = seeded["user"], seeded["mozzarella"]
        steady_history(db, user, product, "2.00", n=6)
        observe(db, user, product, "5.00")
        db.commit()

        anomalies, _, _ = anomaly.price_anomalies(db, user_id=user.id)

        assert len(anomalies) == 1
        assert anomalies[0].direction == "above"
        assert anomalies[0].unit_price_eur == Decimal("5.00")
        assert anomalies[0].median_eur == Decimal("2.00")

    def test_a_bargain_is_reported_too(self, db, seeded):
        # A savings app that only reported bad news would be missing half its own subject.
        user, product = seeded["user"], seeded["mozzarella"]
        steady_history(db, user, product, "5.00", n=6)
        observe(db, user, product, "2.00")
        db.commit()

        anomalies, _, _ = anomaly.price_anomalies(db, user_id=user.id)
        assert len(anomalies) == 1
        assert anomalies[0].direction == "below"

    def test_a_price_in_line_with_history_is_silent(self, db, seeded):
        user, product = seeded["user"], seeded["mozzarella"]
        for i, price in enumerate(["2.00", "2.05", "1.95", "2.10", "1.98", "2.02"]):
            observe(db, user, product, price, days_ago=8 - i)
        observe(db, user, product, "2.01")
        db.commit()

        anomalies, _, _ = anomaly.price_anomalies(db, user_id=user.id)
        assert anomalies == []

    def test_the_judged_price_is_excluded_from_its_own_comparison_window(self, db, seeded):
        # "Trailing" is literal. Including the outlier in the median it is measured against
        # would let it pull the median toward itself and partly conceal its own deviation.
        user, product = seeded["user"], seeded["mozzarella"]
        steady_history(db, user, product, "2.00", n=6)
        observe(db, user, product, "5.00")
        db.commit()

        anomalies, _, _ = anomaly.price_anomalies(db, user_id=user.id)
        # The median is the jittered base, untouched by the 5.00 being judged against it.
        # Had the outlier been inside its own window it would have dragged this upward.
        assert anomalies[0].median_eur == Decimal("2.00")
        # `observations` is the whole series including the judged price; the window behind
        # it is one shorter.
        assert anomalies[0].observations == 7

    def test_history_is_grouped_per_store_not_pooled(self, db, seeded):
        # The same cheese costs different amounts in two shops. Pooling them would make
        # every visit to the pricier one look like an anomaly.
        user, product = seeded["user"], seeded["mozzarella"]
        steady_history(db, user, product, "2.00", n=6, store="Coop")
        observe(db, user, product, "5.00", store="Esselunga")
        db.commit()

        anomalies, examined, _ = anomaly.price_anomalies(db, user_id=user.id)
        assert examined == 2
        assert anomalies == []  # Esselunga has one observation, far below the threshold

    def test_an_unvarying_history_does_not_divide_by_zero(self, db, seeded):
        # Six *identical* prices give a robust sigma of 0 — common for a staple always
        # bought at the same shop. A naive z-score would make every other price infinitely
        # anomalous, so the detector stays silent instead of dividing by zero.
        user, product = seeded["user"], seeded["mozzarella"]
        for i in range(6):
            observe(db, user, product, "2.00", days_ago=7 - i)
        observe(db, user, product, "5.00")
        db.commit()

        anomalies, _, _ = anomaly.price_anomalies(db, user_id=user.id)
        assert anomalies == []


class TestDemoQuarantine:
    def test_demo_observations_are_excluded_by_default(self, db, seeded):
        user, product = seeded["user"], seeded["mozzarella"]
        steady_history(db, user, product, "2.00", n=6)
        observe(db, user, product, "5.00")
        db.commit()

        report = anomaly.report(db, user_id=user.id, include_demo=False)
        assert report.demo_included is False
        # Provenance travels with the number, as everywhere else in this API.
        assert hasattr(report, "demo_observations")


class TestBatchAdvice:
    def test_no_advice_without_enough_observations(self, db, seeded):
        user, product = seeded["user"], seeded["pasta"]
        observe(db, user, product, "2.00")
        db.commit()

        assert anomaly.batch_advice(db, user_id=user.id) == []

    def test_advice_carries_the_waste_gate_it_passed(self, db, seeded):
        # Whatever comes out must state the modelled P(consume before expiry) and the
        # threshold it cleared, so the recommendation can be argued with rather than
        # just believed.
        user, product = seeded["user"], seeded["pasta"]
        steady_history(db, user, product, "2.00", n=6)
        db.commit()

        for advice in anomaly.batch_advice(db, user_id=user.id):
            assert advice.p_consume_large >= advice.threshold
            assert advice.assumed_rate_divisor > 1


class TestReportShape:
    def test_an_empty_database_reports_cleanly(self, db, seeded):
        report = anomaly.report(db, user_id=seeded["user"].id)

        assert report.observations == 0
        assert report.anomalies == []
        assert report.advice == []
        assert report.notes
