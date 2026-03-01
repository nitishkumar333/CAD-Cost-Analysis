import argparse
import yaml
import sys

def main():
    parser = argparse.ArgumentParser(description="Master DFM Checker")
    parser.add_argument("step_file", help="Path to the .step or .stp file")
    parser.add_argument("--config", help="YAML config file", required=True)
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    
    process_type = cfg.get("process_type", "").lower()

    if process_type == "cnc":
        print(f"Running CNC DFM Checks on {args.step_file}...")
        import cnc_dfm_checker
        sys.argv = [sys.argv[0], args.step_file, "--config", args.config]
        cnc_dfm_checker.main()
    elif process_type == "sheet_metal":
        print(f"Running Sheet Metal DFM Checks on {args.step_file}...")
        import sheet_dfm_checker
        sys.argv = [sys.argv[0], args.step_file, "--config", args.config]
        sheet_dfm_checker.main()
    else:
        print(f"Error: Unknown process_type '{process_type}' in config.yaml. Please set it to 'cnc' or 'sheet_metal'.")
        sys.exit(1)

if __name__ == "__main__":
    main()
