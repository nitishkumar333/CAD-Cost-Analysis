# import argparse
# import yaml
# import sys, os

# def main():
#     parser = argparse.ArgumentParser(description="Master DFM Checker")
#     parser.add_argument("step_file", help="Path to the .step or .stp file")
#     args = parser.parse_args()

#     config_file_path = "config.yaml"

#     if not os.path.exists(config_file_path):
#         print(f"❌ Config file not found: {config_file_path}")
#         print("Please create a config.yaml file before running the analyzer.")
#         sys.exit(1)

#     try:
#         with open(config_file_path, "r") as f:
#             cfg = yaml.safe_load(f) or {}
#     except Exception as e:
#         print(f"❌ Failed to load config file: {e}")
#         sys.exit(1)
    
#     process_type = cfg.get("process_type").lower()

#     if process_type == "cnc":
#         print(f"Running CNC DFM Checks on {config_file_path}...")
#         from cnc_dfm_analyzer import CNCAnalyzer
#         try:
#             analyzer = CNCAnalyzer(args.step_file, config=cfg)
#             analyzer.analyze()
#         except Exception as e:
#             print(f"Error processing {args.step_file}: {e}")
#     elif process_type == "sheet_metal":
#         print(f"Running Sheet Metal DFM Checks on {config_file_path}...")
#         from sheet_dfm_analyzer import SheetMetalAnalyzer
#         try:
#             analyzer = SheetMetalAnalyzer(args.step_file, config=cfg)
#             analyzer.analyze()
#         except Exception as e:
#             print(f"Error processing {args.step_file}: {e}")
#     else:
#         print(f"Error: Unknown process_type '{process_type}' in config.yaml. Please set it to 'cnc' or 'sheet_metal'.")
#         sys.exit(1)

# if __name__ == "__main__":
#     main()

import argparse
import yaml
import sys, os

def main():
    config_file_path = "config.yaml"
    step_folder = "original_step_files"

    if not os.path.exists(config_file_path):
        print(f"❌ Config file not found: {config_file_path}")
        print("Please create a config.yaml file before running the analyzer.")
        sys.exit(1)

    if not os.path.exists(step_folder):
        print(f"❌ STEP folder not found: {step_folder}")
        sys.exit(1)

    try:
        with open(config_file_path, "r") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"❌ Failed to load config file: {e}")
        sys.exit(1)

    process_type = cfg.get("process_type", "").lower()

    # collect step files
    step_files = [
        os.path.join(step_folder, f)
        for f in os.listdir(step_folder)
        if f.lower().endswith((".step", ".stp"))
    ]

    if not step_files:
        print("⚠️ No STEP files found in step_files folder.")
        return

    for step_file in step_files:
        print(f"\n📦 Processing: {step_file}")

        if process_type == "cnc":
            print("Running CNC DFM Checks...")
            from cnc_dfm_analyzer import CNCAnalyzer
            try:
                analyzer = CNCAnalyzer(step_file, config=cfg)
                analyzer.analyze()
            except Exception as e:
                print(f"❌ Error processing {step_file}: {e}")

        elif process_type == "sheet_metal":
            print("Running Sheet Metal DFM Checks...")
            from sheet_dfm_analyzer import SheetMetalAnalyzer
            try:
                analyzer = SheetMetalAnalyzer(step_file, config=cfg)
                analyzer.analyze()
                # analyzer.analyze(output_md="dfm_report.md")
            except Exception as e:
                print(f"❌ Error processing {step_file}: {e}")

        else:
            print(f"❌ Unknown process_type '{process_type}' in config.yaml.")
            sys.exit(1)


if __name__ == "__main__":
    main()