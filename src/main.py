"""Centre Point Sweep Order Matching Pipeline - Main Orchestrator"""

import time
from pipeline.pipeline_config import parse_arguments, build_runtime_config, setup_directories
from pipeline.pipeline_output import print_pipeline_header, print_execution_summary
from pipeline.pipeline_stages import execute_pipeline_stages


def main():
    """Main pipeline: 4-stage architecture with --stage argument support."""
    start_time = time.time()
    
    # Parse CLI arguments
    args = parse_arguments()
    
    # Build runtime configuration (CLI overrides config)
    runtime_config = build_runtime_config(args)
    
    # Print header and configuration
    print_pipeline_header(runtime_config)
    
    # Setup directories
    setup_directories()
    
    # Execute pipeline stages
    data, all_partition_keys = execute_pipeline_stages(runtime_config)
    
    # Print summary
    execution_time = time.time() - start_time
    print_execution_summary(data, runtime_config, execution_time)


if __name__ == '__main__':
    main()
