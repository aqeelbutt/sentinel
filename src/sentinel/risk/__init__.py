"""Risk gate. Every order must pass RiskManager.evaluate() and carry its
token; VirtualBroker refuses orders without one. This structural barrier is
what makes 'accidentally skipped risk' a type error."""
