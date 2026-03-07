export default function HomePage() {
  return (
    <main className="welcome-page">
      <section className="hero">
        <p className="badge">Softplan</p>
        <h1>{"\u8ba9\u8f6f\u4ef6\u5de5\u4f5c\u91cf\u8bc4\u4f30\u66f4\u53ef\u9760\u3001\u66f4\u900f\u660e\u3002"}</h1>
        <p className="hero-subtitle">
          {
            "\u901a\u8fc7\u6e05\u6670\u7684\u4f30\u7b97\u6d41\u7a0b\uff0c\u5feb\u901f\u5f97\u5230\u5de5\u4f5c\u91cf\u3001\u9884\u7b97\u4e0e\u4ea4\u4ed8\u5468\u671f\u5efa\u8bae\uff0c\u5e76\u660e\u786e\u5173\u952e\u5047\u8bbe\u4e0e\u5f71\u54cd\u56e0\u7d20\u3002"
          }
        </p>

        <div className="hero-actions">
          <button type="button" className="btn btn-primary">
            {"\u5f00\u59cb\u4f30\u7b97"}
          </button>
          <button type="button" className="btn btn-secondary">
            {"\u67e5\u770b\u65b9\u6cd5\u8bf4\u660e"}
          </button>
        </div>
      </section>

      <section className="feature-grid" aria-label={"Softplan \u6838\u5fc3\u80fd\u529b"}>
        <article className="feature-card">
          <h2>{"\u7cbe\u51c6"}</h2>
          <p>
            {
              "\u7edf\u4e00\u8f93\u5165\u53e3\u5f84\u4e0e\u4f30\u7b97\u7ed3\u6784\uff0c\u4fbf\u4e8e\u56e2\u961f\u6a2a\u5411\u6bd4\u8f83\u65b9\u6848\u5e76\u5f62\u6210\u53ef\u8ffd\u6eaf\u51b3\u7b56\u3002"
            }
          </p>
        </article>
        <article className="feature-card">
          <h2>{"\u6d41\u7545"}</h2>
          <p>
            {
              "\u6309\u6b65\u9aa4\u5f15\u5bfc\u4ece\u5047\u8bbe\u5230\u7ed3\u679c\uff0c\u652f\u6301\u4ea7\u54c1\u3001\u7814\u53d1\u4e0e\u7ba1\u7406\u89d2\u8272\u9ad8\u6548\u534f\u540c\u3002"
            }
          </p>
        </article>
        <article className="feature-card">
          <h2>{"\u900f\u660e"}</h2>
          <p>
            {
              "\u6e05\u6670\u5c55\u793a\u7f6e\u4fe1\u5ea6\u4e0e\u5173\u952e\u53d8\u91cf\uff0c\u8ba9\u8bc4\u5ba1\u805a\u7126\u4f9d\u636e\u800c\u4e0d\u662f\u731c\u6d4b\u3002"
            }
          </p>
        </article>
      </section>

      <section className="kpi-strip" aria-label={"\u9879\u76ee\u5feb\u7167"}>
        <div>
          <p className="kpi-label">{"\u6a21\u578b\u8986\u76d6"}</p>
          <p className="kpi-value">COCOMO II + FP</p>
        </div>
        <div>
          <p className="kpi-label">{"\u5efa\u8bae\u751f\u6210\u901f\u5ea6"}</p>
          <p className="kpi-value">{"< 60 \u79d2"}</p>
        </div>
        <div>
          <p className="kpi-label">{"\u51b3\u7b56\u53ef\u8bfb\u6027"}</p>
          <p className="kpi-value">{"\u7b26\u5408 WCAG AA"}</p>
        </div>
      </section>
    </main>
  );
}
