"""AgentOS Host Bridge — the only component outside Docker. Off by default, token + approval gated.

Features (each opt-in): real-desktop control (pywinauto/pyautogui/mss) and launching your Chrome with a CDP port so
the sandboxed gateway can drive it. Contract: docs/CONTRACTS.md §5.
"""
