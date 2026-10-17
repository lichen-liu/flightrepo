"""Small cancellable prompts shared by local command-line tools."""


def ask(label, default=None):
    while True:
        suffix = f" [{default}]" if default is not None else ""
        value = input(f"{label}{suffix}: ").strip()
        if value.lower() == "cancel":
            raise EOFError
        if value or default is not None:
            return value or default
        print("A value is required.")
