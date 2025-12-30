from tzara import agent

def run_cli():
    tzara_agent = agent.Tzara()

    print("Tzara is online. Type 'exit' to quit.\n")

    while True:
        text = input("You: ").strip()
        if text.lower() in ("exit", "quit"):
            print("Tzara: Goodbye.")
            break

        print("Tzara:", tzara_agent.handle(text), "\n")