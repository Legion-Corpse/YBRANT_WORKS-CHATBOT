from app.chat import guards


def test_identity_intent_detected():
    for msg in [
        "who are you",
        "what are you?",
        "who is this",
        "are you a bot",
        "what can you do",
        "introduce yourself",
    ]:
        assert guards.has_identity_intent(msg), msg


def test_identity_intent_does_not_catch_content_questions():
    for msg in [
        "what are your services",
        "who are your clients",
        "what do you do",
        "how can I contact you",
        "what are your products",
    ]:
        assert not guards.has_identity_intent(msg), msg


# The invented-figure guard now takes the retrieved-context text as a plain
# string (the concatenated file_search results), not a list of chunks.

def test_answer_inventing_figures_blocked():
    ctx = "We build custom software for enterprises."
    assert guards.answer_invents_figures("A typical project costs $10,000.", ctx)
    assert guards.answer_invents_figures("Around 50,000 rupees per month.", ctx)


def test_answer_with_context_figures_allowed():
    ctx = "Our workshop costs $500 per seat."
    assert not guards.answer_invents_figures("The workshop costs $500 per seat.", ctx)


def test_answer_with_equivalent_figure_formatting_allowed():
    ctx = "Implementation packages start at $5,000."
    assert not guards.answer_invents_figures("Implementation starts at $5000.", ctx)


def test_answer_without_figures_allowed():
    ctx = "We build custom software."
    assert not guards.answer_invents_figures("We build software for many industries.", ctx)


def test_worded_amount_blocked():
    ctx = "We build custom software for enterprises."
    assert guards.answer_invents_figures("It costs about five thousand dollars.", ctx)
    assert guards.answer_invents_figures("Roughly ten lakh rupees per project.", ctx)


def test_bare_price_context_number_blocked():
    ctx = "We build custom software for enterprises."
    assert guards.answer_invents_figures("The fee is 25,000 for that.", ctx)
    assert guards.answer_invents_figures("Each project is priced at 1.5k.", ctx)


def test_plain_count_not_mistaken_for_price():
    ctx = "We offer many services."
    assert not guards.answer_invents_figures("We have around 5 service lines.", ctx)
    assert not guards.answer_invents_figures("Founded in 2010, we serve 200 clients.", ctx)


def test_empty_context_does_not_block():
    # With no retrieved context we can't verify, so we must NOT block (avoids
    # false positives); the service runs a final check when context is available.
    assert not guards.answer_invents_figures("A project costs $10,000.", "")
