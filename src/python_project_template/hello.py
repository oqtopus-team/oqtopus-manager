class Hello:
    """A simple class to demonstrate a greeting message."""

    def __init__(self, name: str) -> None:
        """Initialize the Hello class with a name.

        Args:
            name: The name to greet.

        """
        self.name = name

    def greet(self) -> str:
        """Return a greeting message.

        Returns:
            A greeting message.

        """
        return f"Hello, {self.name}!"
