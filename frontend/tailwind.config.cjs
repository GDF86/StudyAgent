module.exports = {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  corePlugins: { preflight: false },
  theme: { extend: {
    colors: { console: {
      canvas: '#eef5ef', surface: '#ffffff', primary: '#28745a', hover: '#1e5e48',
      heading: '#203b30', body: '#4d6558', muted: '#73867b', link: '#286c53', danger: '#bd504b'
    } },
    boxShadow: {
      panel: '0 2px 14px rgba(27,66,46,0.05), inset 0 0 0 rgba(255,255,255,0.5)',
      'panel-hover': '0 4px 20px rgba(27,66,46,0.09), inset 0 0 12px rgba(255,255,255,0.5)'
    }
  } },
  plugins: []
}
