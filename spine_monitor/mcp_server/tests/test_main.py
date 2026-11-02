from spine_mcp_server import main


def test_main_prints_its_greeting(capsys):
    main()
    assert capsys.readouterr().out == "Hello from spine-mcp-server!\n"
