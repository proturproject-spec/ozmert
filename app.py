from flask import Flask, render_template

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html', aktif_sayfa='index')

@app.route('/muhasebe')
def muhasebe():
    return render_template('muhasebe.html', aktif_sayfa='muhasebe')

@app.route('/finans')
def finans():
    return render_template('finans.html', aktif_sayfa='finans')

@app.route('/raporlar')
def raporlar():
    return render_template('raporlar.html', aktif_sayfa='raporlar')

@app.route('/parametreler')
def parametreler():
    return render_template('parametreler.html', aktif_sayfa='parametreler')

if __name__ == '__main__':
    app.run(debug=True)