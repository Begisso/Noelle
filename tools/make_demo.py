"""Generate the Demo project (projects/Demo). Run: python tools/make_demo.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from noelle.demo import ROOT, create_demo  # noqa: E402

if __name__ == "__main__":
    create_demo(sys.argv[1] if len(sys.argv) > 1 else ROOT)
