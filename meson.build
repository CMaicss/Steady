project('steady', version: '1.2.0', meson_version: '>=0.62')

dependency('gtk4', version: '>=4.12')
dependency('libadwaita-1', version: '>=1.5')
python = import('python').find_installation('python3')
package_dir = get_option('prefix') / get_option('datadir') / 'steady'
install_subdir('steady', install_dir: package_dir, exclude_directories: ['__pycache__'])
configuration = configuration_data()
configuration.set('PACKAGE_DIR', "'" + package_dir + "'")
configuration.set('EXEC', 'steady')
configuration.set('SERVICE_EXEC', '"' + (get_option('prefix') / get_option('bindir') / 'steady') + '"')
configure_file(input: 'data/steady.in', output: 'steady', configuration: configuration,
  install: true, install_dir: get_option('bindir'), install_mode: 'rwxr-xr-x')
configure_file(input: 'data/io.github.steady.Steady.desktop.in', output: 'io.github.steady.Steady.desktop',
  configuration: configuration, install: true, install_dir: get_option('datadir') / 'applications')
configure_file(input: 'data/io.github.steady.Steady.service.in', output: 'io.github.steady.Steady.service',
  configuration: configuration, install: true, install_dir: get_option('datadir') / 'dbus-1/services')
install_data('data/io.github.steady.Steady.metainfo.xml', install_dir: get_option('datadir') / 'metainfo')
install_data('steady/icons/hicolor/scalable/apps/io.github.steady.Steady.svg',
  install_dir: get_option('datadir') / 'icons/hicolor/scalable/apps')
test('unit', python, args: ['-m', 'unittest', 'discover', '-s', meson.project_source_root() / 'tests'],
  env: ['PYTHONPATH=' + meson.project_source_root()])
