"""Entry point of phenix-app-cherrymountain."""

from .app import CherryMountain


def main() -> None:
    CherryMountain.main("cherrymountain")


if __name__ == "__main__":
    main()
