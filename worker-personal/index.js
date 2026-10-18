// Your personal Pages project, behind Cloudflare Access (docs/SETUP.md section 9).
const ORIGIN = "https://REPLACE-ME.pages.dev";
const ORIGIN_HOST = new URL(ORIGIN).hostname;

export default {
  async fetch(request, env) {
    const incoming = new URL(request.url);
    const target = new URL(incoming.pathname + incoming.search, ORIGIN);
    const headers = new Headers(request.headers);
    headers.set("CF-Access-Client-Id", env.ACCESS_CLIENT_ID);
    headers.set("CF-Access-Client-Secret", env.ACCESS_CLIENT_SECRET);
    headers.delete("cookie");
    headers.delete("host");
    headers.delete("cf-access-jwt-assertion");
    headers.delete("cf-access-authenticated-user-email");

    const response = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
      redirect: "manual",
    });

    const out = new Headers(response.headers);
    out.delete("set-cookie");
    const location = out.get("location");
    if (location) {
      const redirected = new URL(location, ORIGIN);
      if (redirected.hostname === ORIGIN_HOST) {
        redirected.protocol = incoming.protocol;
        redirected.hostname = incoming.hostname;
        redirected.port = incoming.port;
        out.set("location", redirected.toString());
      }
    }

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: out,
    });
  },
};
