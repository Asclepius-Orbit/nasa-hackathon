from spine_agent import main


def test_main_prints_its_greeting(capsys):
    main()
    assert capsys.readouterr().out == "Hello from spine-agent!\n"
