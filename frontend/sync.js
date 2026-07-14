(function() {
  var SYNC_KEYS = ['image-annotation-mvp-v1'];
  
  try {
    var xhr = new XMLHttpRequest();
    xhr.open('GET', '/api/data', false); // synchronous request
    xhr.send(null);
    
    if (xhr.status === 200) {
      var data = JSON.parse(xhr.responseText);
      for (var i = 0; i < SYNC_KEYS.length; i++) {
        var key = SYNC_KEYS[i];
        if (data[key] !== undefined && data[key] !== null) {
          // Temporarily bypass our own override below (though it hasn't been defined yet)
          localStorage.setItem(key, data[key]);
        }
      }
    }
  } catch (e) {
    console.error('Failed to sync initial state from server', e);
  }

  var originalSetItem = localStorage.setItem;
  localStorage.setItem = function(key, value) {
    originalSetItem.apply(this, arguments);
    
    if (SYNC_KEYS.indexOf(key) !== -1) {
      fetch('/api/data', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: key, value: value }),
        keepalive: true
      }).catch(function(err) {
        console.error('Failed to sync ' + key + ' to server', err);
      });
    }
  };

  var originalClear = localStorage.clear;
  localStorage.clear = function() {
    originalClear.apply(this, arguments);
    
    // Also notify server to clear the workspace keys if needed
    var defaultValues = {
      'image-annotation-mvp-v1': ''
    };
    for (var i = 0; i < SYNC_KEYS.length; i++) {
      var key = SYNC_KEYS[i];
      fetch('/api/data', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: key, value: defaultValues[key] }),
        keepalive: true
      }).catch(function(err) {});
    }
  };
})();
