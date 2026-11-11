// Use the visible HOME scrollport on mobile, the document on other screens.
async function installPageScroll(context) {
  await context.addInitScript(() => {
    window.pivotScrollRoot = () => {
      const home = document.querySelector('[data-home-state]');
      return home?.getClientRects().length && getComputedStyle(home).overflowY === 'auto'
        ? home : document.scrollingElement;
    };
  });
}
module.exports = { installPageScroll };
