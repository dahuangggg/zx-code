def greet(name: str) -> str:
    return f"hello, {name}"


def main():
    xs = [1, 2, 3]
    ys = [x * 2 for x in xs if x % 2 == 1]
    print(greet("world"))
    print(ys)


if __name__ == "__main__":
    main()
