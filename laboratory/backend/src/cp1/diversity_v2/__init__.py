"""Uniform-parent behavior search. Historical diversity-1 remains unchanged."""
VERSION = 'diversity-2'
KINDS = ('numeric', 'local')
METHODS = ('independent', 'reservoir', 'elite', *KINDS)
