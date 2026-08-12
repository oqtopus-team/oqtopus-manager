from python_project_template.hello import Hello


def test_greet_returns_expected_message() -> None:
    """Hello.greet should return the correct greeting."""
    hello = Hello("World")
    assert hello.greet() == "Hello, World!"


def test_greet_with_custom_name() -> None:
    """Hello.greet should include the provided name."""
    hello = Hello("OQTOPUS")
    assert hello.greet() == "Hello, OQTOPUS!"
