"""Behavior-guided testing; kept separate from the historical adaptive-1 study."""

VERSION = "diversity-1"
KINDS = ("events", "senders", "numeric", "messages", "combined")
METHODS = ("independent", "reservoir", "elite", *KINDS)
