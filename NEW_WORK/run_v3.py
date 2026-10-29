from new_work.gui.app import main
import multiprocessing


if __name__ == "__main__":
    multiprocessing.freeze_support()
    # V3 uses the HTML/CSS sender introduced in the current V2 transfer flow.
    main("v2")
