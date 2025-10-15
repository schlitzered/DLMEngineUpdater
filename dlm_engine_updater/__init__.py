import argparse

from dlm_engine_updater.updater import DlmEngineUpdater


def main():
    parser = argparse.ArgumentParser(description="DLM Updater")

    parser.add_argument(
        "--cfg",
        dest="cfg",
        action="store",
        default="/etc/dlm_engine_updater/.env",
        help="Full path to configuration",
    )

    parser.add_argument(
        "--after_reboot",
        dest="rbt",
        action="store_true",
        default=False,
        help="has to be used from init systems, to indicate that the script was called while booting.",
    )

    parser.add_argument(
        "--date_constraint",
        dest="date_constraint",
        action="store",
        default=None,
        help="exit if date constraint is not fulfilled."
        "example value: 3:Friday"
        "would only run if this is the 3rd Friday of a month.",
    )

    parser.add_argument(
        "--random_sleep",
        dest="random_sleep",
        action="store",
        required=False,
        default=0,
        type=int,
        help="add random sleep before acutally doing something",
    )

    parsed_args = parser.parse_args()

    instance = DlmEngineUpdater(
        cfg=parsed_args.cfg,
        after_reboot=parsed_args.rbt,
        date_constraint=parsed_args.date_constraint,
        random_sleep=parsed_args.random_sleep,
    )
    instance.work()


if __name__ == "__main__":
    main()
