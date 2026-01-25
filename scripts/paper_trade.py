#!/usr/bin/env python3
"""
Paper Trading Script

Runs the N-Structure bot in simulation mode:
- No real orders placed
- Signals logged to data/logs/paper_signals.jsonl
- Perfect for strategy validation before going live
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "src"))

from src.main import main


if __name__ == "__main__":
    # Force paper mode by injecting argument
    if "--paper" not in sys.argv:
        sys.argv.append("--paper")
    
    print("=" * 60)
    print("  N-STRUCTURE PAPER TRADING MODE")
    print("  No real orders will be placed")
    print("  Signals logged to: data/logs/paper_signals.jsonl")
    print("=" * 60)
    print()
    
    asyncio.run(main())
