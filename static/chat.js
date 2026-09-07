(function () {
function waitForParticipants() {
const originalParticipants = window.participants;

if (typeof originalParticipants !== 'function') {
  setTimeout(waitForParticipants, 300);
  return;
}

window.participants = async function () {
  await originalParticipants();

  setTimeout(() => {
    document.querySelectorAll('.profileCard').forEach(card => {
      if (card.querySelector('.chat-open-btn')) return;

      const onclick = card.getAttribute('onclick') || '';
      const match = onclick.match(/openProfile\((\d+)/);

      if (!match) return;

      const userId = match[1];

      const btn = document.createElement('button');
      btn.className = 'chat-open-btn primary';
      btn.style.marginTop = '10px';
      btn.textContent = '💬 Xabar yozish';

      btn.onclick = function (e) {
        e.stopPropagation();
        location.href = '/static/chat.html?user_id=' + userId;
      };

      card.appendChild(btn);
    });
  }, 100);
};

}

waitForParticipants();
})();
