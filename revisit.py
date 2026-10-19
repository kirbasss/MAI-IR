"""Run due rechecks of previously processed pages without seeding new URLs."""

from crawler import main


if __name__ == "__main__":
    raise SystemExit(main(mode="revisit"))
