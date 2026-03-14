import sys, os
# Ensure src/ and test/ are on the path for pytest runs from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "test"))
