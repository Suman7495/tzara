"""
Command-line interface for Tzara personal assistant.
"""
from tzara import agent


def run_cli() -> None:
    """
    Run the Tzara CLI loop.

    Continuously prompts the user for input, streams output
    from the agent, and exits cleanly on 'exit' or 'quit'.
    """
    tzara_agent = agent.Tzara(model="llama3.1:8b")

    print("Tzara is online. Type 'exit' to quit.\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ("exit", "quit"):
            print("Tzara: Goodbye.")
            break
      
        # Print Tzara label once
        print("\nTzara: ", end="", flush=True)

        for token in tzara_agent.handle(user_input):
            print(token, end="", flush=True)
        print("\n")  # End of response
