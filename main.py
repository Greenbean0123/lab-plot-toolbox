"""Lab Plot Toolbox / 科研绘图工具箱: TEM, XRD and UV-Vis plotting in one window.

Run:  python main.py              (opens the last used tool)
      python main.py xrd          (xrd | tem | uvvis)
      python main.py --self-test report.json   (unattended check of a packaged build)
"""
import sys

if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--apply-update':  # run by the updater helper copy
        from labstudio.update.installer import run as apply_update
        sys.exit(apply_update(sys.argv[2]))
    if len(sys.argv) >= 2 and sys.argv[1] == '--self-test':
        from labstudio.selftest import run
        sys.exit(run(sys.argv[2] if len(sys.argv) > 2 else 'selftest-report.json'))
    from labstudio.app import main
    main()
