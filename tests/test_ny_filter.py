"""
Tests for ny_filter.py — frontend New York jurisdiction filter.

check_ny_filter(question) returns (is_blocked: bool, message: str).
  is_blocked=False  → query is allowed (NY or ambiguous)
  is_blocked=True   → query is rejected (non-NY or non-legal)
  message           → empty string when not blocked, refusal message when blocked
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ny_filter import check_ny_filter


# ── Allow cases ───────────────────────────────────────────────────────────────

def test_explicit_new_york_allowed():
    blocked, msg = check_ny_filter("My client slipped in a store in New York")
    assert blocked is False
    assert msg == ""

def test_nyc_abbreviation_allowed():
    blocked, _ = check_ny_filter("Car accident happened in NYC last week")
    assert blocked is False

def test_manhattan_allowed():
    blocked, _ = check_ny_filter("Incident occurred on Broadway in Manhattan")
    assert blocked is False

def test_brooklyn_allowed():
    blocked, _ = check_ny_filter("Slip and fall at a warehouse in Brooklyn")
    assert blocked is False

def test_sdny_allowed():
    blocked, _ = check_ny_filter("Filing in SDNY federal court on diversity jurisdiction")
    assert blocked is False

def test_cplr_allowed():
    blocked, _ = check_ny_filter("What are the CPLR statute of limitations rules?")
    assert blocked is False

def test_no_location_defaults_to_ny():
    """Queries with no geographic signal default to NY — allow."""
    blocked, msg = check_ny_filter("My client was injured in a car accident")
    assert blocked is False
    assert msg == ""

def test_no_location_legal_question_allowed():
    blocked, _ = check_ny_filter("What is the standard of negligence in a slip and fall case?")
    assert blocked is False

def test_buffalo_ny_allowed():
    blocked, _ = check_ny_filter("Personal injury lawsuit filed in Buffalo")
    assert blocked is False


# ── Block: non-NY US states ───────────────────────────────────────────────────

def test_california_blocked():
    blocked, msg = check_ny_filter("My client was injured in Los Angeles, California")
    assert blocked is True
    assert "New York" in msg

def test_texas_blocked():
    blocked, msg = check_ny_filter("Contract dispute in Dallas, Texas")
    assert blocked is True
    assert "New York" in msg

def test_florida_blocked():
    blocked, msg = check_ny_filter("Slip and fall at a Miami hotel")
    assert blocked is True

def test_new_jersey_blocked():
    """new jersey must not be confused with new york."""
    blocked, msg = check_ny_filter("Car accident on the New Jersey Turnpike")
    assert blocked is True

def test_new_mexico_blocked():
    """new mexico must not be confused with new york."""
    blocked, msg = check_ny_filter("Property dispute in Albuquerque, New Mexico")
    assert blocked is True

def test_massachusetts_blocked():
    blocked, _ = check_ny_filter("Filing in Boston, Massachusetts court")
    assert blocked is True


# ── Block: foreign jurisdictions ─────────────────────────────────────────────

def test_canada_blocked():
    blocked, msg = check_ny_filter("Car accident in Toronto, Canada")
    assert blocked is True
    assert "New York" in msg

def test_india_blocked():
    blocked, _ = check_ny_filter("Contract dispute under Indian law in Mumbai")
    assert blocked is True

def test_uk_blocked():
    blocked, _ = check_ny_filter("Employment dispute under English law in London")
    assert blocked is True

def test_australia_blocked():
    blocked, _ = check_ny_filter("Personal injury claim in Sydney, Australia")
    assert blocked is True


# ── Block: non-legal queries ──────────────────────────────────────────────────

def test_car_repair_blocked():
    blocked, msg = check_ny_filter("How do I fix my car engine?")
    assert blocked is True
    assert "legal" in msg.lower()

def test_cooking_blocked():
    blocked, msg = check_ny_filter("What is the best recipe for pasta carbonara?")
    assert blocked is True

def test_it_help_blocked():
    blocked, msg = check_ny_filter("My laptop won't connect to WiFi")
    assert blocked is True


# ── NY takes precedence over non-NY ──────────────────────────────────────────

def test_ny_beats_new_jersey_in_same_query():
    """If both NY and NJ appear, NY signal should win → allow."""
    blocked, _ = check_ny_filter(
        "Case involves events in both New York and New Jersey"
    )
    assert blocked is False

def test_ny_beats_california_in_same_query():
    """Multi-jurisdiction query with explicit NY → allow."""
    blocked, _ = check_ny_filter(
        "Federal case filed in New York involving a California defendant"
    )
    assert blocked is False
