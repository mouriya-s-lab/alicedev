/*
 * alicedev share view boot (ARCHITECTURE §8).
 *
 * Injected by the gateway as the first script of every proxied Paseo HTML
 * document, so it runs before Paseo's (deferred) bundle.  It makes every page
 * load a clean self-hosted boot, working around two Paseo frontend bugs that
 * together break any direct load of a host route:
 *
 *  - with no host in the persisted registry, the registry is marked ready
 *    before the /_paseo/hosts.json probe finishes, so /h/<server>/… redirects
 *    to /welcome → /open-project and the deep link is lost;
 *  - with a host in the persisted registry, boot probes the same manifest
 *    connection again and the second connection releases the first one's
 *    subscriptions (timeline stuck on "Updating messages").
 *
 * So: drop the persisted registry (every host comes from the manifest), and
 * turn a host route into Paseo's own startup restore — remember the workspace
 * as the last selection and start from "/"; Paseo waits for the host and then
 * opens that workspace.  The agent named by ?open=agent:<id> is re-applied the
 * first time Paseo navigates to that workspace.
 *
 * Bound to the deployed Paseo version (storage keys, route shapes): re-verify
 * with agent-browser after every Paseo upgrade, with paseo-view.css.
 */
(function () {
  "use strict";
  var REGISTRY_KEY = "@paseo:daemon-registry";
  var LAST_WORKSPACE_KEY = "paseo:last-workspace-route-selection";
  var HOST_ROUTE = /^\/h\/([^/]+)(?:\/workspace\/([^/]+))?/;

  try {
    window.localStorage.removeItem(REGISTRY_KEY);
  } catch (error) {
    return;
  }

  var match = window.location.pathname.match(HOST_ROUTE);
  if (!match) {
    return;
  }
  var serverId = decodeURIComponent(match[1]);
  var workspaceId = match[2] ? decodeURIComponent(match[2]) : null;
  var open = new URLSearchParams(window.location.search).get("open");

  if (workspaceId) {
    window.localStorage.setItem(
      LAST_WORKSPACE_KEY,
      JSON.stringify({ serverId: serverId, workspaceId: workspaceId })
    );
  }

  if (workspaceId && open) {
    // Paseo's restore navigates to the bare workspace route; add ?open= back
    // on that first navigation only.
    var target = "/h/" + match[1] + "/workspace/" + match[2];
    var withOpen = target + "?open=" + encodeURIComponent(open);
    var patch = function (name) {
      var original = window.history[name];
      window.history[name] = function (state, title, url) {
        if (typeof url === "string" && url === target) {
          window.history.pushState = originalPush;
          window.history.replaceState = originalReplace;
          url = withOpen;
        }
        return original.call(window.history, state, title, url);
      };
    };
    var originalPush = window.history.pushState;
    var originalReplace = window.history.replaceState;
    patch("pushState");
    patch("replaceState");
    originalReplace.call(window.history, window.history.state, "", "/");
    return;
  }

  window.history.replaceState(window.history.state, "", "/");
})();
