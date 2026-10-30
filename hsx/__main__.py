from doctor import print_report, run_doctor


def main() -> int:
    return print_report(run_doctor())


if __name__ == "__main__":
    raise SystemExit(main())
