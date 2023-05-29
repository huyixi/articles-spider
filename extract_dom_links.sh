# 提示输入文件名
echo "请输入 DOM 元素所在的文件名(该文件需放置在同一目录下)： "
read FILE_NAME

# 读取文件中的内容
DOM=$(cat "$FILE_NAME")

# 提取 href 属性
hrefs=$(echo $DOM | grep -o '<a href=['"'"'"][^"'"'"']*['"'"'"]' | sed -e 's/^<a href=["'"'"']//' -e 's/["'"'"']$//')

# 输出第一个获取到的 href
first_href=$(echo "$hrefs" | head -n 1)
echo "第一个获取到的 href 是：$first_href"

# 提示是否添加前缀
echo "是否要添加前缀？ (若要添加请直接输入前缀，若输入为空，则视为不添加) "
read PREFIX

# 如果用户没有输入任何内容，则将前缀设置为空
PREFIX=${PREFIX:-''}

hrefs=$(echo "$hrefs" | while read -r line ; do
    if echo "$line" | grep -q -e '^http://' -e '^https://'; then
        # 如果已经包含 http:// 或 https://，则直接输出
        echo "$line"
    else
        # 如果没有，则添加前缀
        echo "${PREFIX}$line"
    fi
done)

# 输出结果到一个新的文件，文件名为原文件名加上 "_links"
echo "$hrefs" > "links_${FILE_NAME}"
