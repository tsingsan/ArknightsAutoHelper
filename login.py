import sys
from Arknights.shell_next import _create_helper

if __name__ == '__main__':

    if len(sys.argv) > 2:

        helper, context = _create_helper()
        with context:
            helper.login(sys.argv[1], sys.argv[2])
    else:
        sys.exit(1) #No Username / Password