from PIL import Image
img = Image.new('RGB', (80, 80))
colors = [(255,0,0),(0,200,0),(0,0,255),(255,200,0),(180,0,180)]
for row in range(8):
    for col in range(8):
        c = colors[(row+col) % len(colors)]
        for dy in range(10):
            for dx in range(10):
                img.putpixel((col*10+dx, row*10+dy), c)
img.save('test_art.png')