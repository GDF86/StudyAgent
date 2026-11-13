import pytest
from start_web_demo import parse_options

def test_named_session_is_explicit():
    args=parse_options(['--enable-authorized-web-demo','--session-name','demo-20261110-01'])
    assert args.session_name=='demo-20261110-01'

@pytest.mark.parametrize('argv',[[],['--session-name','demo'],['--enable-authorized-web-demo','--session-name','bad/name'],['--enable-authorized-web-demo','--session-name','demo','--verified-output-fix']])
def test_invalid_or_ambiguous_start_is_rejected(argv):
    with pytest.raises(SystemExit):parse_options(argv)
