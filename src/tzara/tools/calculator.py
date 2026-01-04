from langchain.tools import tool
import math

@tool
def calculator(expression: str) -> str:
    """
    Evaluate a mathematical expression.

    Use ONLY if the user asks to calculate, compute, add, subtract,
    multiply, divide, or evaluate an expression.

    Examples:
    - "what is 12 * 7"
    - "calculate (5 + 3) * 2"
    - "compute sqrt(16)"

    Input:
    - expression: a valid Python math expression

    Do NOT use for:
    - explanations
    - word problems
    - general conversation
    """
    try:
        return str(eval(expression, {"__builtins__": {}}, vars(math)))
    except Exception as e:
        return f"Error: {e}"
