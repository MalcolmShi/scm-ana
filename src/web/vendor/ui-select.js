/* 统一下拉组件：把原生 select 包装成可定制的 listbox。
   - 原生 select 保留在 DOM 里作为唯一数据源，value / change / options 照常可用；
   - 菜单挂到 body 上并 position:fixed，避免被页头的层叠上下文或 overflow 裁掉；
   - 选项动态变化（appendChild / replaceChildren / innerHTML）时自动重建。 */
(function () {
  'use strict';

  var valueDescriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
  var openInstance = null;
  var seq = 0;

  function chevron() {
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'ui-select-arrow');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    var path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', 'm6 9 6 6 6-6');
    svg.appendChild(path);
    return svg;
  }

  function enhance(select) {
    if (!select || select.dataset.uiSelect === 'on') return null;
    select.dataset.uiSelect = 'on';

    var wrap = document.createElement('div');
    wrap.className = 'ui-select' + (select.dataset.align === 'end' ? ' ui-select--end' : '');
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);
    select.classList.add('ui-select-native');
    select.tabIndex = -1;

    var trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'ui-select-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    if (select.getAttribute('aria-label')) trigger.setAttribute('aria-label', select.getAttribute('aria-label'));

    var label = document.createElement('span');
    label.className = 'ui-select-label';
    trigger.appendChild(label);
    trigger.appendChild(chevron());

    var menu = document.createElement('div');
    menu.className = 'ui-select-menu';
    menu.id = 'ui-select-menu-' + (++seq);
    menu.setAttribute('role', 'listbox');
    menu.hidden = true;
    trigger.setAttribute('aria-controls', menu.id);
    if (select.getAttribute('aria-label')) menu.setAttribute('aria-label', select.getAttribute('aria-label'));

    wrap.appendChild(trigger);
    document.body.appendChild(menu);

    function syncLabel() {
      var option = select.options[select.selectedIndex];
      label.textContent = option ? option.textContent.trim() : (select.dataset.placeholder || '请选择');
      label.title = label.textContent;
      trigger.disabled = select.disabled;
    }

    function markSelected() {
      for (var i = 0; i < menu.children.length; i++) {
        menu.children[i].setAttribute('aria-selected', String(i === select.selectedIndex));
      }
    }

    function buildOptions() {
      var fragment = document.createDocumentFragment();
      Array.prototype.forEach.call(select.options, function (option, index) {
        var item = document.createElement('button');
        item.type = 'button';
        item.className = 'ui-select-option';
        item.setAttribute('role', 'option');
        item.tabIndex = -1;
        item.textContent = option.textContent.trim();
        item.disabled = option.disabled;
        item.addEventListener('click', function () { choose(index); });
        fragment.appendChild(item);
      });
      menu.replaceChildren(fragment);
      markSelected();
    }

    function visibleItems() {
      return Array.prototype.filter.call(menu.children, function (item) { return !item.disabled; });
    }

    function position() {
      var anchor = trigger.getBoundingClientRect();
      var box = menu.getBoundingClientRect();
      var width = box.width, height = box.height;
      var vw = document.documentElement.clientWidth || window.innerWidth;
      var vh = document.documentElement.clientHeight || window.innerHeight;
      var left = wrap.classList.contains('ui-select--end') ? anchor.right - width : anchor.left;
      left = Math.max(8, Math.min(left, vw - width - 8));
      var top = anchor.bottom + 6;
      if (top + height > vh - 8 && anchor.top - height - 6 >= 8) top = anchor.top - height - 6;
      menu.style.left = Math.round(left) + 'px';
      menu.style.top = Math.round(Math.max(8, top)) + 'px';
    }

    function keepInView(item) {
      var top = item.offsetTop, bottom = top + item.offsetHeight;
      if (top < menu.scrollTop) menu.scrollTop = Math.max(0, top - 4);
      else if (bottom > menu.scrollTop + menu.clientHeight) menu.scrollTop = bottom - menu.clientHeight + 4;
    }

    function focusItem(item) {
      if (!item) return;
      Array.prototype.forEach.call(menu.children, function (child) { child.classList.remove('is-active'); });
      item.classList.add('is-active');
      item.focus({ preventScroll: true });
      keepInView(item);
    }

    function choose(index) {
      var option = select.options[index];
      if (!option || option.disabled) return;
      select.selectedIndex = index;
      syncLabel();
      markSelected();
      close(true);
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function onScroll() {
      var anchor = trigger.getBoundingClientRect();
      var vh = document.documentElement.clientHeight || window.innerHeight;
      if (anchor.bottom < 0 || anchor.top > vh) { close(false); return; }
      position();
    }

    function open() {
      if (!menu.hidden) return;
      if (openInstance && openInstance !== instance) openInstance.close(false);
      buildOptions();
      menu.hidden = false;
      menu.style.minWidth = Math.round(trigger.getBoundingClientRect().width) + 'px';
      menu.style.left = '-9999px';
      menu.style.top = '0px';
      position();
      wrap.classList.add('is-open');
      trigger.setAttribute('aria-expanded', 'true');
      openInstance = instance;
      window.addEventListener('scroll', onScroll, true);
      window.addEventListener('resize', onScroll);
      var items = visibleItems();
      var current = Array.prototype.indexOf.call(items, menu.children[select.selectedIndex]);
      focusItem(items[current >= 0 ? current : 0]);
    }

    function close(refocus) {
      if (menu.hidden) return;
      menu.hidden = true;
      menu.style.minWidth = '';
      wrap.classList.remove('is-open');
      trigger.setAttribute('aria-expanded', 'false');
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onScroll);
      if (openInstance === instance) openInstance = null;
      if (refocus) trigger.focus({ preventScroll: true });
    }

    var instance = { close: close, el: wrap, menu: menu };

    trigger.addEventListener('click', function (event) {
      event.preventDefault();
      if (menu.hidden) open(); else close(true);
    });

    trigger.addEventListener('keydown', function (event) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        if (menu.hidden) {
          open();
          if (event.key === 'ArrowUp') {
            var items = visibleItems();
            focusItem(items[items.length - 1]);
          }
        } else {
          close(true);
        }
      }
    });

    menu.addEventListener('keydown', function (event) {
      var items = visibleItems();
      var current = items.indexOf(document.activeElement);
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        var next = event.key === 'ArrowDown'
          ? Math.min(items.length - 1, current + 1)
          : Math.max(0, current - 1);
        focusItem(items[next]);
      } else if (event.key === 'Home' || event.key === 'End') {
        event.preventDefault();
        focusItem(items[event.key === 'Home' ? 0 : items.length - 1]);
      } else if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        close(true);
      } else if (event.key === 'Tab') {
        close(false);
      }
    });

    select.addEventListener('change', syncLabel);

    Object.defineProperty(select, 'value', {
      configurable: true,
      get: function () { return valueDescriptor.get.call(select); },
      set: function (next) {
        valueDescriptor.set.call(select, next);
        syncLabel();
        markSelected();
      }
    });

    new MutationObserver(function () {
      syncLabel();
      if (!menu.hidden) { buildOptions(); position(); }
    }).observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled'] });

    syncLabel();
    return instance;
  }

  document.addEventListener('click', function (event) {
    if (!openInstance) return;
    if (openInstance.el.contains(event.target) || openInstance.menu.contains(event.target)) return;
    openInstance.close(false);
  }, true);

  function boot() {
    Array.prototype.forEach.call(document.querySelectorAll('select'), enhance);
    new MutationObserver(function (records) {
      records.forEach(function (record) {
        Array.prototype.forEach.call(record.addedNodes, function (node) {
          if (node.nodeType !== 1) return;
          if (node.tagName === 'SELECT') enhance(node);
          else if (node.querySelectorAll) Array.prototype.forEach.call(node.querySelectorAll('select'), enhance);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();

  window.UISelect = { enhance: enhance };
})();
